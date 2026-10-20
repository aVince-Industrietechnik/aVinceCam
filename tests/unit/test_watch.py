# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""watch: camera use from inotify and /proc – simulated sysfs, plain files as nodes."""

import builtins
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import fakesysfs
from avincecam import sysfs, watch

FRONT = "sensor:i2c-INT3479:00"
BACK = "sensor:i2c-INT3477:00"
WEBCAM = "usb:1-5"
CORE = os.path.join(os.path.dirname(__file__), "..", "..", "core", "avincecam-core")


make_dev = fakesysfs.make_dev


class WatchBase(unittest.TestCase):
    scenario = staticmethod(fakesysfs.ipu3_plus_uvc)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys = os.path.join(self.tmp.name, "sys")
        self.root = os.path.join(self.tmp.name, "root")
        self.ipa = os.path.join(self.tmp.name, "ipa")
        for path in (self.sys, self.root, self.ipa):
            os.makedirs(path)
        self.scenario(self.sys)
        make_dev(self.sys, self.root)
        self.cameras, self.owners = watch.camera_map(self.sys, self.root, (self.ipa,))

    def tearDown(self):
        self.tmp.cleanup()

    def dev(self, name):
        return os.path.join(self.root, "dev", name)

    def node_of(self, camera_id, prefix):
        camera = next(c for c in self.cameras if c.id == camera_id)
        return next(os.path.basename(n) for n in camera.nodes
                    if os.path.basename(n).startswith(prefix))


class CameraMapTest(WatchBase):
    def test_own_nodes_belong_to_one_camera(self):
        self.assertEqual(self.owners[self.dev(self.node_of(WEBCAM, "video"))], [WEBCAM])
        self.assertEqual(self.owners[self.dev(self.node_of(FRONT, "v4l-subdev"))], [FRONT])
        self.assertEqual(self.owners[self.dev(self.node_of(BACK, "v4l-subdev"))], [BACK])

    def test_pipeline_nodes_are_shared(self):
        shared = [ids for ids in self.owners.values() if len(ids) > 1]
        self.assertTrue(shared)
        for ids in shared:
            self.assertEqual(sorted(ids), sorted([FRONT, BACK]))

    def test_lens_motor_is_not_watched(self):
        # The fake sysfs has a dw9714 node; nothing of the motor is listed.
        lens = [n for n in sysfs.read_nodes(self.sys) if n["driver"] == "dw9714"]
        for node in lens:
            self.assertNotIn(self.dev(node["node"]), self.owners)


class UsageTest(WatchBase):
    def test_nobody(self):
        self.assertEqual(watch.usage(self.owners, exclude_pids=()), [])

    def test_webcam_in_use(self):
        with open(self.dev(self.node_of(WEBCAM, "video"))):
            users = watch.usage(self.owners, exclude_pids=())
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["pid"], os.getpid())
        self.assertEqual(users[0]["cameras"], [WEBCAM])
        self.assertTrue(users[0]["certain"])
        self.assertFalse(users[0]["own_preview"])

    def test_only_shared_nodes_cannot_tell_the_camera(self):
        shared = next(p for p, ids in self.owners.items() if len(ids) > 1)
        with open(shared):
            users = watch.usage(self.owners, exclude_pids=())
        self.assertEqual(users[0]["cameras"], sorted([BACK, FRONT]))
        self.assertFalse(users[0]["certain"])

    def test_sensor_node_tells_the_camera(self):
        shared = next(p for p, ids in self.owners.items() if len(ids) > 1)
        with open(shared), open(self.dev(self.node_of(FRONT, "v4l-subdev"))):
            users = watch.usage(self.owners, exclude_pids=())
        self.assertEqual(users[0]["cameras"], [FRONT])
        self.assertTrue(users[0]["certain"])

    def test_own_preview_is_marked(self):
        with open(self.dev(self.node_of(WEBCAM, "video"))):
            users = watch.usage(self.owners, exclude_pids=(), own_preview_pid=os.getpid())
        self.assertTrue(users[0]["own_preview"])

    def test_child_of_own_preview_is_own(self):
        """A snapshot runs cam as a child process of the locked core process."""
        node = self.dev(self.node_of(WEBCAM, "video"))
        child = subprocess.Popen([sys.executable, "-c",
                                  f"import time; f=open({node!r}); print('ok', flush=True); time.sleep(30)"],
                                 stdout=subprocess.PIPE, text=True)
        try:
            child.stdout.readline()
            users = watch.usage(self.owners, exclude_pids=(os.getpid(),),
                                own_preview_pid=os.getpid())
            self.assertEqual([u["pid"] for u in users], [child.pid])
            self.assertTrue(users[0]["own_preview"])
        finally:
            child.kill()
            child.wait()
            child.stdout.close()

    def test_excluded_pid_is_ignored(self):
        with open(self.dev(self.node_of(WEBCAM, "video"))):
            self.assertEqual(watch.usage(self.owners, exclude_pids=(os.getpid(),)), [])


class WatcherTest(WatchBase):
    def start(self):
        self.reports = []
        self.watcher = watch.Watcher(self.reports.append, sysfs_root=self.sys,
                                     dev_root=self.root, exclude_pids=(),
                                     debounce=0.05, ipa_dirs=(self.ipa,))
        self.stop_r, self.stop_w = os.pipe()
        self.thread = threading.Thread(target=self.watcher.run,
                                       kwargs={"stop_fd": self.stop_r, "timeout": 10})
        self.thread.start()
        self.wait_for(lambda: len(self.reports) >= 1)

    def tearDown(self):
        if hasattr(self, "thread"):
            os.close(self.stop_w)
            self.thread.join(5)
            self.watcher.close()
            os.close(self.stop_r)
        super().tearDown()

    def wait_for(self, condition, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(0.01)
        self.fail(f"condition not reached, reports: {self.reports}")

    def test_reports_start_open_and_close(self):
        self.start()
        self.assertEqual(self.reports[0], [])
        handle = open(self.dev(self.node_of(WEBCAM, "video")))
        self.wait_for(lambda: len(self.reports) >= 2)
        self.assertEqual(self.reports[1][0]["cameras"], [WEBCAM])
        handle.close()
        self.wait_for(lambda: len(self.reports) >= 3)
        self.assertEqual(self.reports[2], [])

    def test_short_open_is_not_reported(self):
        self.start()
        self.watcher.debounce = 0.3
        with open(self.dev(self.node_of(WEBCAM, "video"))):
            pass
        time.sleep(0.6)
        self.assertEqual(self.reports, [[]])

    def test_stops_when_input_closes(self):
        self.start()
        os.close(self.stop_w)
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.stop_w = os.open(os.devnull, os.O_RDONLY)  # closed again in tearDown

    def test_never_opens_a_device_node(self):
        real_open, real_os_open = builtins.open, os.open
        dev = os.path.join(self.root, "dev")

        def guarded(opener):
            def wrapper(path, *args, **kwargs):
                if isinstance(path, (str, bytes)) and os.fsdecode(path).startswith(dev):
                    if threading.current_thread() is not threading.main_thread():
                        raise AssertionError(f"watch opened {path}")
                return opener(path, *args, **kwargs)
            return wrapper

        with mock.patch("builtins.open", guarded(real_open)), \
                mock.patch("os.open", guarded(real_os_open)):
            self.start()
            with real_open(self.dev(self.node_of(WEBCAM, "video"))):
                self.wait_for(lambda: len(self.reports) >= 2)


class HotplugTest(WatchBase):
    scenario = staticmethod(fakesysfs.latitude_5285)

    def test_new_webcam_is_watched(self):
        reports = []
        watcher = watch.Watcher(reports.append, sysfs_root=self.sys, dev_root=self.root,
                                exclude_pids=(), debounce=0.05, ipa_dirs=(self.ipa,))
        stop_r, stop_w = os.pipe()
        thread = threading.Thread(target=watcher.run, kwargs={"stop_fd": stop_r, "timeout": 10})
        thread.start()
        try:
            fakesysfs.usb_webcam(self.sys, first_video=20, media="media2")
            make_dev(self.sys, self.root)  # the new nodes appear in /dev
            time.sleep(0.3)
            with open(self.dev("video20")):
                end = time.monotonic() + 5
                while time.monotonic() < end and len(reports) < 2:
                    time.sleep(0.01)
            self.assertEqual(reports[1][0]["cameras"], [WEBCAM])
        finally:
            os.close(stop_w)
            thread.join(5)
            watcher.close()
            os.close(stop_r)


class WatchCliTest(WatchBase):
    def test_json_lines_until_stdin_closes(self):
        env = dict(os.environ, AVINCECAM_NO_HARDWARE="1")
        proc = subprocess.Popen(
            [sys.executable, "-I", CORE, "watch", "--sysfs-root", self.sys,
             "--dev-root", self.root, "--runtime-dir", os.path.join(self.tmp.name, "run")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        try:
            first = json.loads(proc.stdout.readline())
            self.assertEqual(first["command"], "watch")
            self.assertEqual(first["users"], [])
            handle = open(self.dev(self.node_of(WEBCAM, "video")))
            second = json.loads(proc.stdout.readline())
            self.assertEqual(second["users"][0]["pid"], os.getpid())
            self.assertEqual(second["users"][0]["cameras"], [WEBCAM])
            handle.close()
            proc.stdin.close()
            self.assertEqual(proc.wait(10), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(10)
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
