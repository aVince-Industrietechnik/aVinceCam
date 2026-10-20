# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""install.sh / uninstall.sh in a fake home – the real home is never touched."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTALL = os.path.join(REPO, "scripts", "install.sh")
UNINSTALL = os.path.join(REPO, "scripts", "uninstall.sh")
UUID = "avincecam@avince"


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.run_dir = os.path.join(self.tmp.name, "run")
        os.makedirs(self.home)
        os.makedirs(self.run_dir)
        self.env = dict(os.environ, HOME=self.home, XDG_RUNTIME_DIR=self.run_dir,
                        XDG_CACHE_HOME=os.path.join(self.home, ".cache"))
        self.applet = os.path.join(self.home, ".local/share/cinnamon/applets", UUID)
        self.mo = os.path.join(self.home, ".local/share/locale/de/LC_MESSAGES", UUID + ".mo")
        self.manifest = os.path.join(self.home, ".local/share/avincecam/installed.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, script, *args):
        return subprocess.run(["bash", script, *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_install_copies_applet_core_and_translation(self):
        r = self.run_script(INSTALL)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("applet.js", "metadata.json", "icon.png", "icons/avincecam.svg",
                     "core/avincecam-core", "core/avincecam/__main__.py"):
            self.assertTrue(os.path.exists(os.path.join(self.applet, name)), name)
        self.assertFalse(os.path.islink(self.applet))
        self.assertTrue(os.path.isfile(self.mo) or shutil.which("msgfmt") is None)
        self.assertFalse([p for p, _, _ in os.walk(self.applet) if "__pycache__" in p])

    def test_installed_core_runs(self):
        self.run_script(INSTALL)
        core = os.path.join(self.applet, "core", "avincecam-core")
        r = subprocess.run([sys.executable, "-I", core, "version"],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(r.stdout)["command"], "version")

    def test_uninstall_removes_exactly_what_was_installed(self):
        self.run_script(INSTALL)
        other = os.path.join(self.home, ".local/share/cinnamon/applets/other@x")
        os.makedirs(other)
        r = self.run_script(UNINSTALL, "--no-panel-check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.applet))
        self.assertFalse(os.path.exists(self.mo))
        self.assertFalse(os.path.exists(self.manifest))
        self.assertTrue(os.path.isdir(other), "other applets must stay")

    def test_settings_kept_unless_purge(self):
        self.run_script(INSTALL)
        settings = os.path.join(self.home, ".config/cinnamon/spices", UUID)
        os.makedirs(settings)
        self.run_script(UNINSTALL, "--no-panel-check")
        self.assertTrue(os.path.isdir(settings))
        self.run_script(INSTALL)
        self.run_script(UNINSTALL, "--no-panel-check", "--purge")
        self.assertFalse(os.path.exists(settings))

    def test_reinstall_backs_up_previous_version(self):
        self.run_script(INSTALL)
        marker = os.path.join(self.applet, "old-marker")
        open(marker, "w").close()
        r = self.run_script(INSTALL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(marker))
        backups = os.path.join(self.home, ".local/share/avincecam/backups")
        found = [p for p, _, files in os.walk(backups) if "old-marker" in files]
        self.assertTrue(found, "previous installation must be backed up")

    def test_development_link_is_never_replaced(self):
        os.makedirs(os.path.dirname(self.applet))
        os.symlink(os.path.join(REPO, "applet", UUID), self.applet)
        r = self.run_script(INSTALL)
        self.assertEqual(r.returncode, 1)
        self.assertIn("development link", r.stderr)
        self.assertTrue(os.path.islink(self.applet))

    def test_uninstall_ignores_paths_outside_home(self):
        self.run_script(INSTALL)
        outside = os.path.join(self.tmp.name, "outside.txt")
        open(outside, "w").close()
        with open(self.manifest, "a") as f:
            f.write(outside + "\n")
        r = self.run_script(UNINSTALL, "--no-panel-check")
        self.assertIn("skipped (outside home)", r.stdout)
        self.assertTrue(os.path.exists(outside))

    def test_uninstall_without_installation(self):
        r = self.run_script(UNINSTALL, "--no-panel-check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to remove", r.stdout)


if __name__ == "__main__":
    unittest.main()
