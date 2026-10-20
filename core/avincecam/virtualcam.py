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
This process therefore sees only the few plugins it needs: they are linked
into a private plugin folder and GStreamer uses a private registry, so the
video4linux2 plugin is never loaded.
"""

import os
import signal
import sys

# Plugins the pipeline needs (file names in the GStreamer plugin folder).
PLUGINS = ("libgstcoreelements.so", "libgstlibcamera.so",
           "libgstvideoconvertscale.so", "libgstpipewire.so")
PLUGIN_DIRS = ("/usr/lib/x86_64-linux-gnu/gstreamer-1.0",
               "/usr/lib/aarch64-linux-gnu/gstreamer-1.0",
               "/usr/lib64/gstreamer-1.0", "/usr/lib/gstreamer-1.0")
# A format every PipeWire camera consumer understands.
OUTPUT_FORMAT = "I420"


class VirtualCameraError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def find_plugin_dir(candidates=PLUGIN_DIRS):
    for directory in candidates:
        if os.path.isfile(os.path.join(directory, "libgstlibcamera.so")):
            return directory
    return None


def prepare_plugins(private_dir, system_dir):
    """Link exactly the needed plugins into `private_dir`. Returns the list."""
    os.makedirs(private_dir, mode=0o700, exist_ok=True)
    for name in os.listdir(private_dir):
        if name not in PLUGINS:
            os.remove(os.path.join(private_dir, name))
    missing = [p for p in PLUGINS if not os.path.isfile(os.path.join(system_dir, p))]
    if missing:
        package = ("gstreamer1.0-libcamera" if "libgstlibcamera.so" in missing else
                   "gstreamer1.0-pipewire" if "libgstpipewire.so" in missing else
                   "gstreamer1.0-plugins-base")
        raise VirtualCameraError("plugin-missing", "a GStreamer plugin is missing",
                                 f"{', '.join(missing)} (package {package})")
    for name in PLUGINS:
        link = os.path.join(private_dir, name)
        target = os.path.join(system_dir, name)
        if os.path.islink(link) and os.readlink(link) == target:
            continue
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(target, link)
    return sorted(os.listdir(private_dir))


def gst_environment(private_dir, registry):
    """Environment that limits GStreamer to the private plugin folder."""
    return {
        "GST_PLUGIN_SYSTEM_PATH_1_0": private_dir,
        "GST_PLUGIN_SYSTEM_PATH": private_dir,
        "GST_PLUGIN_PATH_1_0": "",
        "GST_PLUGIN_PATH": "",
        "GST_REGISTRY_1_0": registry,
        "GST_REGISTRY": registry,
    }


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
    system_dir = find_plugin_dir()
    if system_dir is None:
        print("aVinceCam: GStreamer libcamera plugin not found "
              "(package gstreamer1.0-libcamera)", file=sys.stderr)
        return 1
    private_dir = os.path.join(runtime_dir, "gst-plugins")
    try:
        prepare_plugins(private_dir, system_dir)
    except VirtualCameraError as exc:
        print(f"aVinceCam: {exc.message}: {exc.detail}", file=sys.stderr)
        return 1
    os.environ.update(gst_environment(private_dir, os.path.join(runtime_dir, "gst-registry.bin")))

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
