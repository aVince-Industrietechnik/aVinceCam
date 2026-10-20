# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Virtual camera: plugin isolation, pipeline and start through the preview.

GStreamer is never loaded here: the pipeline is built against a stand-in
for the Gst module, and the preview runs the fake viewer.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from avincecam import preview, virtualcam
from avincecam.model import Camera
from test_preview import CORE, FRONT, BACK, PreviewBase

FRONT_ID = r"\_SB_.PCI0.LNK0"


class PluginIsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.system = os.path.join(self.tmp.name, "system")
        self.private = os.path.join(self.tmp.name, "private")
        os.makedirs(self.system)
        for name in virtualcam.PLUGINS + ("libgstvideo4linux2.so", "libgstautodetect.so"):
            open(os.path.join(self.system, name), "w").close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_needed_plugins_are_linked(self):
        linked = virtualcam.prepare_plugins(self.private, self.system)
        self.assertEqual(linked, sorted(virtualcam.PLUGINS))
        self.assertNotIn("libgstvideo4linux2.so", linked)
        for name in linked:
            self.assertEqual(os.readlink(os.path.join(self.private, name)),
                             os.path.join(self.system, name))

    def test_stray_plugin_is_removed(self):
        os.makedirs(self.private)
        os.symlink(os.path.join(self.system, "libgstvideo4linux2.so"),
                   os.path.join(self.private, "libgstvideo4linux2.so"))
        self.assertNotIn("libgstvideo4linux2.so",
                         virtualcam.prepare_plugins(self.private, self.system))

    def test_missing_plugin_names_the_package(self):
        os.remove(os.path.join(self.system, "libgstpipewire.so"))
        with self.assertRaises(virtualcam.VirtualCameraError) as ctx:
            virtualcam.prepare_plugins(self.private, self.system)
        self.assertEqual(ctx.exception.code, "plugin-missing")
        self.assertIn("gstreamer1.0-pipewire", ctx.exception.detail)

    def test_environment_points_only_to_private_folder(self):
        env = virtualcam.gst_environment("/run/p", "/run/r.bin")
        self.assertEqual(env["GST_PLUGIN_SYSTEM_PATH_1_0"], "/run/p")
        self.assertEqual(env["GST_PLUGIN_PATH_1_0"], "")
        self.assertEqual(env["GST_REGISTRY_1_0"], "/run/r.bin")

    def test_find_plugin_dir(self):
        self.assertEqual(virtualcam.find_plugin_dir(("/nonexistent", self.system)), self.system)
        self.assertIsNone(virtualcam.find_plugin_dir(("/nonexistent",)))


class FakeElement:
    def __init__(self, factory):
        self.factory = factory
        self.props = {}
        self.args = {}
        self.linked_to = None

    def set_property(self, name, value):
        self.props[name] = value

    def link(self, other):
        self.linked_to = other
        return True

    def get_name(self):
        return self.factory


class FakeGst:
    """Just enough of the Gst module for build_pipeline()."""

    def __init__(self, missing=()):
        self.made = []
        self.missing = missing
        gst = self

        class Pipeline:
            @staticmethod
            def new(name):
                pipeline = FakeElement("pipeline")
                pipeline.children = []
                pipeline.add = pipeline.children.append
                return pipeline

        class ElementFactory:
            @staticmethod
            def make(factory, _name):
                if factory in gst.missing:
                    return None
                element = FakeElement(factory)
                gst.made.append(element)
                return element

        class Caps:
            @staticmethod
            def from_string(text):
                return text

        self.Pipeline, self.ElementFactory, self.Caps = Pipeline, ElementFactory, Caps

    @staticmethod
    def util_set_object_arg(element, name, value):
        element.args[name] = value


class PipelineTest(unittest.TestCase):
    def test_elements_in_order_and_linked(self):
        gst = FakeGst()
        pipeline = virtualcam.build_pipeline(gst, FRONT_ID, 1280, 720, "Front camera")
        self.assertEqual([e.factory for e in gst.made],
                         ["libcamerasrc", "capsfilter", "videoconvert", "capsfilter",
                          "pipewiresink"])
        self.assertEqual(pipeline.children, gst.made)
        for left, right in zip(gst.made, gst.made[1:]):
            self.assertIs(left.linked_to, right)

    def test_properties(self):
        gst = FakeGst()
        virtualcam.build_pipeline(gst, FRONT_ID, 640, 480, "Front camera")
        source, size, _conv, fmt, sink = gst.made
        self.assertEqual(source.props["camera-name"], FRONT_ID)
        self.assertEqual(size.props["caps"], "video/x-raw,width=640,height=480")
        self.assertEqual(fmt.props["caps"], "video/x-raw,format=I420")
        self.assertEqual(sink.args["mode"], "provide")
        props = sink.args["stream-properties"]
        self.assertIn('media.class=(string)"Video/Source"', props)
        self.assertIn('media.role=(string)"Camera"', props)
        self.assertIn('node.description=(string)"aVinceCam: Front camera"', props)

    def test_no_video4linux_element(self):
        gst = FakeGst()
        virtualcam.build_pipeline(gst, FRONT_ID, 1280, 720, "x")
        self.assertFalse(any("v4l2" in e.factory for e in gst.made))

    def test_quotes_in_name_cannot_break_the_structure(self):
        self.assertNotIn('a"b', virtualcam.stream_properties('a"b'))

    def test_missing_element(self):
        with self.assertRaises(virtualcam.VirtualCameraError) as ctx:
            virtualcam.build_pipeline(FakeGst(missing=("pipewiresink",)), FRONT_ID, 1, 1, "x")
        self.assertEqual(ctx.exception.code, "element-missing")


class ViewerCommandTest(unittest.TestCase):
    def test_libcamera_camera(self):
        cam = Camera(id=FRONT, label="Front camera", api="libcamera", backend_id=FRONT_ID)
        argv = preview.viewer_command(cam, "virtual", (1280, 720))
        self.assertEqual(argv[:3], [sys.executable, "-I", preview.LAUNCHER])
        self.assertEqual(argv[3:], ["virtual-camera", FRONT_ID, "--size", "1280x720",
                                    "--name", "Front camera"])
        self.assertTrue(os.path.isfile(preview.LAUNCHER))

    def test_usb_camera_needs_none(self):
        uvc = Camera(id="usb:1-5", label="Webcam", api="v4l2", nodes=["/dev/video0"])
        with self.assertRaises(preview.PreviewError) as ctx:
            preview.viewer_command(uvc, "virtual", (1280, 720))
        self.assertEqual(ctx.exception.code, "virtual-not-needed")


class VirtualPreviewTest(PreviewBase):
    def test_started_like_a_preview(self):
        proc, answer = self.start(FRONT, "--viewer", "virtual")
        self.assertTrue(answer["started"], answer)
        self.assertEqual(answer["viewer"], "virtual-camera")
        self.wait_ready()
        code, status = self.core("status")
        self.assertEqual(status["preview"]["viewer"], "virtual-camera")
        self.core("stop")
        proc.wait(timeout=10)
        with open(os.path.join(self.run_dir, preview.LOG_NAME)) as log:
            self.assertIn("virtual-camera " + FRONT_ID, log.read())

    def test_lens_camera_stays_locked(self):
        proc, answer = self.start(BACK, "--viewer", "virtual")
        self.assertEqual(answer["errors"][0]["code"], "lens-blocked")
        self.assertEqual(proc.wait(timeout=10), 1)

    def test_blocked_without_hardware(self):
        result = subprocess.run(
            [sys.executable, "-I", CORE, "virtual-camera", FRONT_ID,
             "--runtime-dir", self.run_dir],
            capture_output=True, text=True, timeout=30, env=self.env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("switched off", result.stderr)


if __name__ == "__main__":
    unittest.main()
