#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Angelo Vincenti
"""Watch which processes open camera device nodes (read-only diagnosis).

Two sources, both read-only:
* inotify on /dev/video*, /dev/media*, /dev/v4l-subdev*: exact time of every
  open and close, even very short ones (no process id available here);
* polling /proc/<pid>/fd every 20 ms: which process holds a node open.

Usage: watch_opens.py SECONDS > log
"""

import ctypes
import ctypes.util
import glob
import os
import select
import struct
import sys
import time

IN_OPEN = 0x00000020
IN_CLOSE_WRITE = 0x00000008
IN_CLOSE_NOWRITE = 0x00000010
IN_NONBLOCK = 0o4000

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def stamp():
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d}"


def holders(nodes):
    found = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if target in nodes:
                try:
                    with open(f"/proc/{pid}/comm") as f:
                        comm = f.read().strip()
                except OSError:
                    comm = "?"
                found.add((int(pid), comm, target))
    return found


def main(seconds):
    nodes = sorted(glob.glob("/dev/video*") + glob.glob("/dev/media*") +
                   glob.glob("/dev/v4l-subdev*"))
    fd = libc.inotify_init1(IN_NONBLOCK)
    if fd < 0:
        print("inotify not available", flush=True)
        return 1
    wd_to_node = {}
    for node in nodes:
        wd = libc.inotify_add_watch(fd, node.encode(), IN_OPEN | IN_CLOSE_WRITE | IN_CLOSE_NOWRITE)
        if wd >= 0:
            wd_to_node[wd] = node
    print(f"{stamp()} watching {len(wd_to_node)} nodes for {seconds} s", flush=True)

    end = time.monotonic() + seconds
    known = set()
    while time.monotonic() < end:
        ready, _, _ = select.select([fd], [], [], 0.02)
        if ready:
            data = os.read(fd, 65536)
            i = 0
            while i < len(data):
                wd, mask, _cookie, length = struct.unpack_from("iIII", data, i)
                i += 16 + length
                kind = "OPEN " if mask & IN_OPEN else "close"
                print(f"{stamp()} inotify {kind} {wd_to_node.get(wd, '?')}", flush=True)
        now = holders(set(wd_to_node.values()))
        for pid, comm, node in sorted(now - known):
            print(f"{stamp()} holder + {comm}[{pid}] {node}", flush=True)
        for pid, comm, node in sorted(known - now):
            print(f"{stamp()} holder - {comm}[{pid}] {node}", flush=True)
        known = now
    print(f"{stamp()} done", flush=True)
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 120))
