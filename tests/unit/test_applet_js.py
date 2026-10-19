"""Run the cjs-based applet tests and a syntax check of all applet files."""

import os
import shutil
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPLET = os.path.join(REPO, "applet", "avincecam@avince")
CJS = shutil.which("cjs")

SYNTAX_CHECK = """
const GLib = imports.gi.GLib;
const [ok, bytes] = GLib.file_get_contents(ARGV[0]);
new Function(new TextDecoder().decode(bytes));
"""


@unittest.skipUnless(CJS, "cjs not installed")
class AppletJsTest(unittest.TestCase):
    def test_syntax_of_all_applet_files(self):
        for name in sorted(os.listdir(APPLET)):
            if not name.endswith(".js"):
                continue
            path = os.path.join(APPLET, name)
            result = subprocess.run([CJS, "-c", SYNTAX_CHECK, path],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")

    def test_applet_modules(self):
        result = subprocess.run(
            [CJS, os.path.join(REPO, "tests", "applet", "test_applet.js"), REPO],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 failed", result.stdout)


if __name__ == "__main__":
    unittest.main()
