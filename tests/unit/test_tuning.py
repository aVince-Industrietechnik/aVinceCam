"""Tuning files without autofocus (D16) – no camera, no system file changed."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import fakesysfs
from avincecam import preview, tuning
from avincecam.model import Camera
from test_preview import BACK, FRONT, PreviewBase
from test_snapshot import CORE, FAKE_CAM

UNCALIBRATED = """# SPDX-License-Identifier: CC0-1.0
%YAML 1.1
---
version: 1
algorithms:
  - Af:
  - Agc:
  - Awb:
  - BlackLevelCorrection:
  - ToneMapping:
...
"""

WITH_PARAMETERS = """version: 1
algorithms:
  - Agc:
      target: 0.5
  - Af:
      # sweep settings
      steps: 32
      range:
        - 0
        - 1023
  - Awb:
"""


def system_dirs(root, files):
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    return (root,)


def lens_camera(model="ov8858"):
    return Camera(id=BACK, label="Back camera", model=model, api="libcamera",
                  pipeline="ipu3", has_lens=True, backend_id="x")


class StripTest(unittest.TestCase):
    def test_uncalibrated(self):
        text = tuning.strip_af(UNCALIBRATED)
        self.assertNotIn("Af", text)
        for name in ("Agc", "Awb", "BlackLevelCorrection", "ToneMapping"):
            self.assertIn(f"  - {name}:", text)
        self.assertTrue(text.startswith("# SPDX"))
        self.assertTrue(text.rstrip().endswith("..."))

    def test_parameters_go_with_af(self):
        text = tuning.strip_af(WITH_PARAMETERS)
        self.assertNotIn("Af", text)
        self.assertNotIn("steps", text)
        self.assertNotIn("1023", text)
        self.assertIn("target: 0.5", text)
        self.assertIn("  - Awb:", text)

    def test_nothing_to_strip(self):
        self.assertEqual(tuning.strip_af("algorithms:\n  - Agc:\n"), "algorithms:\n  - Agc:\n")


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys_root = os.path.join(self.tmp.name, "share")
        self.run = os.path.join(self.tmp.name, "run")
        os.makedirs(self.run)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sensor_file_preferred(self):
        dirs = system_dirs(self.sys_root, {"ipu3/uncalibrated.yaml": UNCALIBRATED,
                                           "ipu3/ov8858.yaml": WITH_PARAMETERS})
        self.assertTrue(tuning.source_file("ipu3", "ov8858", dirs).endswith("ov8858.yaml"))
        self.assertTrue(tuning.source_file("ipu3", "ov5670", dirs).endswith("uncalibrated.yaml"))
        self.assertIsNone(tuning.source_file("rkisp1", "ov8858", dirs))

    def test_written_for_lens_camera_only(self):
        dirs = system_dirs(self.sys_root, {"ipu3/uncalibrated.yaml": UNCALIBRATED})
        front = Camera(id=FRONT, label="Front camera", model="ov5670", api="libcamera",
                       pipeline="ipu3", has_lens=False)
        self.assertIsNone(tuning.prepare(self.run, [front], dirs))
        folder = tuning.prepare(self.run, [front, lens_camera()], dirs)
        self.assertEqual(folder, os.path.join(self.run, "ipa-config"))
        self.assertEqual(os.listdir(os.path.join(folder, "ipu3")), ["ov8858.yaml"])
        with open(os.path.join(folder, "ipu3", "ov8858.yaml"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.startswith("# Written by aVinceCam"))
        self.assertNotIn("- Af", text)
        self.assertIn("- Agc", text)

    def test_no_system_file_no_folder(self):
        self.assertIsNone(tuning.prepare(self.run, [lens_camera()], (self.sys_root,)))

    def test_environment_keeps_existing_path(self):
        dirs = system_dirs(self.sys_root, {"ipu3/uncalibrated.yaml": UNCALIBRATED})
        with mock.patch.dict(os.environ, {tuning.ENV: "/somewhere"}):
            env = tuning.environment(self.run, [lens_camera()], dirs)
        self.assertEqual(env[tuning.ENV], os.path.join(self.run, "ipa-config") + ":/somewhere")
        self.assertEqual(tuning.environment(self.run, [], dirs), {})

    def test_system_dirs_override(self):
        system_dirs(self.sys_root, {"ipu3/uncalibrated.yaml": UNCALIBRATED})
        with mock.patch.dict(os.environ, {tuning.SYSTEM_DIRS_ENV: self.sys_root}):
            self.assertIsNotNone(tuning.source_file("ipu3", "ov8858", ("/nonexistent",)))


class ViewerTest(PreviewBase):
    def setUp(self):
        super().setUp()
        self.sys_root = os.path.join(self.tmp.name, "share")
        system_dirs(self.sys_root, {"ipu3/uncalibrated.yaml": UNCALIBRATED})
        self.env = dict(self.env, **{tuning.SYSTEM_DIRS_ENV: self.sys_root})

    def log(self):
        with open(os.path.join(self.run_dir, preview.LOG_NAME)) as handle:
            return handle.read()

    def test_lens_camera_preview_gets_tuning_without_af(self):
        proc, answer = self.start(BACK, "--allow-lens")
        self.assertTrue(answer["started"], answer)
        self.wait_ready()
        self.core("stop")
        proc.wait(timeout=10)
        self.assertIn("fake viewer ipa config: " + os.path.join(self.run_dir, "ipa-config"),
                      self.log())
        with open(os.path.join(self.run_dir, "ipa-config", "ipu3", "ov8858.yaml")) as handle:
            self.assertNotIn("- Af", handle.read())

    def test_virtual_camera_gets_it_too(self):
        proc, answer = self.start(BACK, "--allow-lens", "--viewer", "virtual")
        self.assertTrue(answer["started"], answer)
        self.wait_ready()
        self.core("stop")
        proc.wait(timeout=10)
        self.assertIn("fake viewer ipa config:", self.log())

    def test_front_camera_unchanged(self):
        proc, answer = self.start(FRONT)
        self.wait_ready()
        self.core("stop")
        proc.wait(timeout=10)
        self.assertNotIn("ipa config", self.log())


class SnapshotTest(unittest.TestCase):
    def test_cam_runs_with_tuning_for_lens_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            sys_dir = os.path.join(tmp, "sys")
            os.makedirs(sys_dir)
            fakesysfs.latitude_5285(sys_dir)
            share = os.path.join(tmp, "share")
            system_dirs(share, {"ipu3/uncalibrated.yaml": UNCALIBRATED})
            os.makedirs(os.path.join(tmp, "dev", "dev"))
            env_file = os.path.join(tmp, "cam-env.txt")
            env = dict(os.environ, AVINCECAM_NO_HARDWARE="1", AVINCECAM_FAKE_CAM=FAKE_CAM,
                       FAKE_CAM_ENV_FILE=env_file, **{tuning.SYSTEM_DIRS_ENV: share})
            for camera, expect in ((BACK, True), (FRONT, False)):
                result = subprocess.run(
                    [sys.executable, "-I", CORE, "snapshot", camera, "--size", "64x48",
                     "--allow-lens", "--sysfs-root", sys_dir, "--dev-root", os.path.join(tmp, "dev"),
                     "--runtime-dir", os.path.join(tmp, "run"), "--out", os.path.join(tmp, "out")],
                    capture_output=True, text=True, timeout=60, env=env)
                self.assertEqual(json.loads(result.stdout)["errors"], [], result.stdout)
                with open(env_file, encoding="utf-8") as handle:
                    seen = handle.read()
                self.assertEqual(bool(seen), expect, (camera, seen))


if __name__ == "__main__":
    unittest.main()
