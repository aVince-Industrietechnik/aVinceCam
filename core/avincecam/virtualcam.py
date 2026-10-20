"""Offer a libcamera camera to other programs as a PipeWire camera.

Browsers and video calls (Firefox, Chrome, Teams and Zoom on the web) find
cameras through PipeWire. A raw-sensor camera such as Intel IPU3 only works
through libcamera, so aVinceCam runs a small pipeline

    libcamerasrc -> videoconvert -> pipewiresink (mode=provide)

that appears in PipeWire as a camera "aVinceCam: <name>". Nothing on the
system is changed: no package, no PipeWire or WirePlumber configuration.
The camera runs only while the user has switched this on; it is started
like a preview (lock file, busy check, lens lock) and stopped the same way.

GStreamer's video4linux2 plugin opens every video and sub-device node of
the machine when it is loaded (decision D12) – including the lens motor.
This process therefore sees only the few plugins it needs (gstisolation).
"""

import os
import signal
import sys

from . import gstisolation

# Plugins the pipeline needs (file names in the GStreamer plugin folder).
PLUGINS = ("libgstcoreelements.so", "libgstlibcamera.so",
           "libgstvideoconvertscale.so", "libgstpipewire.so")
# A format every PipeWire camera consumer understands.
OUTPUT_FORMAT = "I420"

VirtualCameraError = gstisolation.IsolationError
find_plugin_dir = gstisolation.find_plugin_dir
gst_environment = gstisolation.environment


def prepare_plugins(private_dir, system_dir):
    """Link exactly the needed plugins into `private_dir`. Returns the list."""
    return gstisolation.prepare_plugins(private_dir, system_dir, PLUGINS)


def stream_properties(name):
    """PipeWire properties of the offered camera (GstStructure text)."""
    description = "aVinceCam: " + name.replace('"', "'")
    return ('props, media.class=(string)"Video/Source", media.role=(string)"Camera", '
            'node.name=(string)"avincecam-virtual-camera", '
            f'node.description=(string)"{description}"')


def build_pipeline(Gst, camera_id, width, height, name):
    """The pipeline as a Gst.Pipeline. `Gst` is passed in for tests."""
    pipeline = Gst.Pipeline.new("avincecam-virtual-camera")
    elements = []
    for factory in ("libcamerasrc", "capsfilter", "videoconvert", "capsfilter",
                    "pipewiresink"):
        element = Gst.ElementFactory.make(factory, None)
        if element is None:
            raise VirtualCameraError("element-missing",
                                     f"the GStreamer element {factory} is not available")
        elements.append(element)
    source, size_caps, convert, format_caps, sink = elements
    source.set_property("camera-name", camera_id)
    size_caps.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,width={width},height={height}"))
    format_caps.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,format={OUTPUT_FORMAT}"))
    sink.set_property("client-name", "aVinceCam")
    Gst.util_set_object_arg(sink, "mode", "provide")
    Gst.util_set_object_arg(sink, "stream-properties", stream_properties(name))
    for element in elements:
        pipeline.add(element)
    for left, right in zip(elements, elements[1:]):
        if not left.link(right):
            raise VirtualCameraError("link-failed", "the pipeline could not be built",
                                     f"{left.get_name()} -> {right.get_name()}")
    return pipeline


def run(camera_id, width, height, name, runtime_dir):
    """Stream until SIGTERM/SIGINT or an error. Returns an exit code.

    Messages go to stderr (the preview log). Must run in a fresh process:
    the GStreamer environment has to be set before GStreamer is loaded.
    """
    try:
        os.environ.update(gstisolation.isolated_environment(runtime_dir, "gst", PLUGINS))
    except gstisolation.IsolationError as exc:
        print(f"aVinceCam: {exc.message}: {exc.detail}", file=sys.stderr)
        return 1

    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst

    Gst.init(None)
    try:
        pipeline = build_pipeline(Gst, camera_id, width, height, name)
    except VirtualCameraError as exc:
        print(f"aVinceCam: {exc.message} {exc.detail or ''}", file=sys.stderr)
        return 1

    loop = GLib.MainLoop()
    result = {"code": 0}

    def on_message(_bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print(f"aVinceCam: {error.message} ({debug})", file=sys.stderr)
            result["code"] = 1
            loop.quit()
        elif message.type == Gst.MessageType.EOS:
            loop.quit()
        return True

    def on_signal():
        loop.quit()
        return GLib.SOURCE_REMOVE

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_message)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, on_signal)

    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        print("aVinceCam: the camera could not be started", file=sys.stderr)
        pipeline.set_state(Gst.State.NULL)
        return 1
    print(f"aVinceCam: offering {camera_id} as PipeWire camera '{name}'", file=sys.stderr)
    try:
        loop.run()
    finally:
        # Releases the camera before the process ends.
        pipeline.set_state(Gst.State.NULL)
    return result["code"]
