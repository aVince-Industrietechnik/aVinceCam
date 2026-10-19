"""Every text of the applet must be in the template and translated to German."""

import json
import os
import re
import shutil
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPLET = os.path.join(REPO, "applet", "avincecam@avince")
PO_DIR = os.path.join(APPLET, "po")

# _("...") with a plain string literal (escaped quotes allowed).
CALL = re.compile(r'\b_\("((?:[^"\\]|\\.)*)"\)')


def unquote(text):
    return json.loads('"' + text + '"')


def po_entries(path):
    """msgid -> msgstr of a .po/.pot file (multi-line strings joined)."""
    entries, key, current = {}, None, None
    msgid, msgstr = [], []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("msgid "):
                if key is not None:
                    entries["".join(msgid)] = "".join(msgstr)
                msgid, msgstr, current, key = [unquote(line[7:-1])], [], "id", True
            elif line.startswith("msgstr "):
                msgstr, current = [unquote(line[8:-1])], "str"
            elif line.startswith('"') and current:
                (msgid if current == "id" else msgstr).append(unquote(line[1:-1]))
    if key is not None:
        entries["".join(msgid)] = "".join(msgstr)
    entries.pop("", None)
    return entries


def code_strings():
    found = set()
    for name in os.listdir(APPLET):
        if name.endswith(".js"):
            with open(os.path.join(APPLET, name), encoding="utf-8") as handle:
                # Comments may mention _("...") without being real texts.
                code = re.sub(r"/\*.*?\*/", "", handle.read(), flags=re.S)
                code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
                found.update(unquote(m) for m in CALL.findall(code))
    return found


class TranslationTest(unittest.TestCase):
    def setUp(self):
        self.pot = po_entries(os.path.join(PO_DIR, "avincecam@avince.pot"))
        self.de = po_entries(os.path.join(PO_DIR, "de.po"))

    def test_template_contains_all_code_strings(self):
        missing = sorted(code_strings() - set(self.pot))
        self.assertEqual(missing, [], "run cinnamon-xlet-makepot . in the applet folder")

    def test_every_template_string_translated_to_german(self):
        missing = sorted(k for k in self.pot if not self.de.get(k))
        self.assertEqual(missing, [])

    def test_placeholders_kept(self):
        for msgid, msgstr in self.de.items():
            if msgstr:
                self.assertEqual(sorted(re.findall(r"%[ds]", msgid)),
                                 sorted(re.findall(r"%[ds]", msgstr)), msgid)

    @unittest.skipUnless(shutil.which("msgfmt"), "msgfmt not installed")
    def test_msgfmt_accepts_german_file(self):
        result = subprocess.run(["msgfmt", "-c", "-o", os.devnull,
                                 os.path.join(PO_DIR, "de.po")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
