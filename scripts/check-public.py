#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Check that the repository contains nothing private.

The repository is meant to become public. Machine-specific paths, backup
locations and internal planning notes belong elsewhere. This script checks
all files tracked or staged in git, plus the commit messages.

Exit code 0 = clean, 1 = findings, 2 = could not run.
"""

import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = os.path.relpath(os.path.abspath(__file__), REPO)

# Built from pieces so that this file does not match itself.
FORBIDDEN = [
    re.compile("/" + "home" + "/[a-z]"),
    re.compile("/" + "mnt" + "/"),
    re.compile("LX" + "-NAS", re.IGNORECASE),
    re.compile("60" + "_SETUP"),
    re.compile("NAECHSTER" + "_SCHRITT"),
    re.compile("PRUEF" + "PROTOKOLL"),
    re.compile("Linux" + "_Mint_Master"),
]


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], check=True,
                          capture_output=True, text=True).stdout


def tracked_files():
    names = set(git("ls-files", "--cached").splitlines())
    names |= set(git("diff", "--cached", "--name-only").splitlines())
    return sorted(n for n in names if n != SELF and os.path.isfile(os.path.join(REPO, n)))


def scan_text(label, text, findings):
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in FORBIDDEN:
            if pattern.search(line):
                findings.append(f"{label}:{number}: {pattern.pattern!r}")


def main():
    try:
        files = tracked_files()
        try:
            messages = git("log", "--all", "--format=%H%n%B%n--end--")
        except subprocess.CalledProcessError:
            messages = ""  # no commits yet
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"check-public: cannot run git: {exc}", file=sys.stderr)
        return 2

    findings = []
    for name in files:
        try:
            with open(os.path.join(REPO, name), encoding="utf-8") as handle:
                scan_text(name, handle.read(), findings)
        except UnicodeDecodeError:
            continue  # binary file
    scan_text("commit messages", messages, findings)

    if findings:
        print("check-public: private content found:")
        for finding in findings:
            print("  " + finding)
        return 1
    print(f"check-public: clean ({len(files)} files, commit messages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
