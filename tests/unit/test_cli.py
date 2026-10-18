import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

from avincecam import SCHEMA_VERSION, __main__ as cli


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(argv)
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None)


class CliTest(unittest.TestCase):
    def test_version(self):
        code, data = run(["version"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(data["schema"], SCHEMA_VERSION)
        self.assertIn("version", data)

    def test_unimplemented_commands_say_so(self):
        for argv in (["probe"], ["status"], ["stop"], ["diagnose"],
                     ["preview", "x"]):
            code, data = run(argv)
            self.assertEqual(code, cli.EXIT_NOT_IMPLEMENTED, argv)
            self.assertEqual(data["errors"][0]["code"], "not-implemented")

    def test_unknown_command_is_usage_error(self):
        code, _ = run(["format-disk"])
        self.assertEqual(code, cli.EXIT_USAGE)


class LauncherTest(unittest.TestCase):
    """The launcher must work in isolated mode, as the applet calls it."""

    def test_isolated_launcher(self):
        launcher = os.path.join(os.path.dirname(__file__), "..", "..",
                                "core", "avincecam-core")
        result = subprocess.run([sys.executable, "-I", launcher, "version"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, cli.EXIT_OK, result.stderr)
        self.assertEqual(json.loads(result.stdout)["command"], "version")


if __name__ == "__main__":
    unittest.main()
