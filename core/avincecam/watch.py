"""Watch which programs use a camera – without opening any camera device.

inotify reports when a device node below /dev is opened or closed. Adding
a watch only looks up the path; the device itself is never opened, so no
camera is powered up and no lens motor is touched. After an event the
holders are read from /proc (busy.holders), the same way the preview checks
whether a camera is free.

Short opens (udev, PipeWire enumerating devices) are filtered out: a state
is reported only when it still holds after a short delay.
"""

import ctypes
import ctypes.util
import errno
import os
import re
import select
import struct
import time

from . import busy, sysfs

# inotify(7)
IN_OPEN = 0x00000020
IN_CLOSE_WRITE = 0x00000008
IN_CLOSE_NOWRITE = 0x00000010
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000
_EVENT = struct.Struct("iIII")

NODE_EVENTS = IN_OPEN | IN_CLOSE_WRITE | IN_CLOSE_NOWRITE
DIR_EVENTS = IN_CREATE | IN_DELETE
NODE_NAME = re.compile(r"^(video|media|v4l-subdev)\d+$")

DEBOUNCE = 0.5


class WatchError(Exception):
    pass


class Inotify:
    """Minimal inotify wrapper (Python has none in the standard library)."""

    def __init__(self):
        self._libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6",
                                 use_errno=True)
        self.fd = self._libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise WatchError(os.strerror(ctypes.get_errno()))

    def add(self, path, mask):
        wd = self._libc.inotify_add_watch(self.fd, os.fsencode(path), mask)
        if wd < 0:
            err = ctypes.get_errno()
            if err in (errno.ENOENT, errno.EACCES):
                return None
            raise WatchError(f"{path}: {os.strerror(err)}")
        return wd

    def remove(self, wd):
        self._libc.inotify_rm_watch(self.fd, wd)

    def read(self):
        """Pending events as a list of (wd, mask, name)."""
        try:
            data = os.read(self.fd, 64 * 1024)
        except BlockingIOError:
            return []
        events = []
        offset = 0
        while offset + _EVENT.size <= len(data):
            wd, mask, _cookie, length = _EVENT.unpack_from(data, offset)
            offset += _EVENT.size
            name = data[offset:offset + length].rstrip(b"\0").decode(errors="replace")
            offset += length
            events.append((wd, mask, name))
        return events

    def close(self):
        os.close(self.fd)


def _dev_path(dev, dev_root):
    return dev if dev_root == "/" else os.path.join(dev_root, dev.lstrip("/"))


def camera_map(sysfs_root, dev_root="/", ipa_dirs=sysfs.DEFAULT_IPA_DIRS):
    """Which node belongs to which camera.

    Returns (cameras, {node path: [camera ids]}). A node that several
    cameras share (the media device and the other nodes of an Intel IPU3
    pipeline) lists all of them.
    """
    cameras, _ = sysfs.scan(sysfs_root, ipa_dirs)
    owners = {}
    pipeline = set()
    for node in sysfs.read_nodes(sysfs_root):
        if node["driver"] in sysfs.PIPELINE_DRIVERS:
            pipeline.add(node["dev"])
    for media in sysfs.read_media(sysfs_root):
        if media["driver"] in sysfs.PIPELINE_DRIVERS:
            pipeline.add(media["dev"])
    libcamera_ids = [c.id for c in cameras if c.api == "libcamera"]
    for camera in cameras:
        for dev in camera.nodes:
            owners.setdefault(_dev_path(dev, dev_root), []).append(camera.id)
    for dev in pipeline:
        owners.setdefault(_dev_path(dev, dev_root), []).extend(
            i for i in libcamera_ids if i not in owners.get(_dev_path(dev, dev_root), []))
    return cameras, owners


def _parent_pid(pid, proc_root):
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), encoding="utf-8") as handle:
            # the command name may contain spaces and ")": parse after the last ")"
            return int(handle.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def usage(owners, proc_root="/proc", exclude_pids=(), own_preview_pid=None):
    """Programs that hold camera nodes open, as a sorted list of dicts.

    Each entry: pid, name, cameras (ids), certain (exactly one camera could
    be told from the open nodes), own_preview.
    """
    users = []
    for holder in busy.holders(list(owners), proc_root, exclude_pids):
        exclusive = set()
        shared = set()
        for dev in holder["devices"]:
            ids = owners.get(dev, [])
            (exclusive if len(ids) == 1 else shared).update(ids)
        users.append({
            "pid": holder["pid"],
            "name": holder["name"],
            "cameras": sorted(exclusive or shared),
            "certain": len(exclusive or shared) == 1,
            # The photo command runs cam as a child: that counts as own use too.
            "own_preview": own_preview_pid is not None and (
                holder["pid"] == own_preview_pid or
                _parent_pid(holder["pid"], proc_root) == own_preview_pid),
        })
    return sorted(users, key=lambda u: u["pid"])


class Watcher:
    """Reports camera use whenever it changes.

    `report(users)` is called once at the start and then after every change.
    `own_preview_pid()` returns the pid of aVinceCam's own preview or None.
    """

    def __init__(self, report, sysfs_root="/sys", dev_root="/", proc_root="/proc",
                 exclude_pids=None, own_preview_pid=lambda: None,
                 debounce=DEBOUNCE, ipa_dirs=sysfs.DEFAULT_IPA_DIRS):
        self.report = report
        self.sysfs_root = sysfs_root
        self.dev_root = dev_root
        self.proc_root = proc_root
        self.exclude_pids = (os.getpid(),) if exclude_pids is None else exclude_pids
        self.own_preview_pid = own_preview_pid
        self.debounce = debounce
        self.ipa_dirs = ipa_dirs
        self.inotify = Inotify()
        self.node_watches = {}
        self.owners = {}
        self.last = None
        dev_dir = _dev_path("/dev", dev_root)
        if self.inotify.add(dev_dir, DIR_EVENTS) is None:
            raise WatchError(f"cannot watch {dev_dir}")
        self._rescan()

    def _rescan(self):
        """Read the cameras from sysfs again and watch exactly their nodes."""
        _cameras, self.owners = camera_map(self.sysfs_root, self.dev_root, self.ipa_dirs)
        wanted = set(self.owners)
        for path in list(self.node_watches):
            if path not in wanted:
                self.inotify.remove(self.node_watches.pop(path))
        for path in sorted(wanted - set(self.node_watches)):
            wd = self.inotify.add(path, NODE_EVENTS)
            if wd is not None:
                self.node_watches[path] = wd

    def check(self, force=False):
        users = usage(self.owners, self.proc_root, self.exclude_pids,
                      self.own_preview_pid())
        if force or users != self.last:
            self.last = users
            self.report(users)

    def run(self, stop_fd=None, timeout=None):
        """Wait for events until stop_fd is readable (or closed) or timeout.

        Returns when stop_fd signals end of input (the applet went away).
        """
        self.check(force=True)
        deadline = None
        rescan = False
        end = None if timeout is None else time.monotonic() + timeout
        fds = [self.inotify.fd] + ([stop_fd] if stop_fd is not None else [])
        while True:
            now = time.monotonic()
            if end is not None and now >= end:
                return
            waits = [t - now for t in (deadline, end) if t is not None]
            ready, _, _ = select.select(fds, [], [], max(0.0, min(waits)) if waits else None)
            if stop_fd is not None and stop_fd in ready:
                if not os.read(stop_fd, 4096):
                    return
            if self.inotify.fd in ready:
                relevant = False
                for _wd, _mask, name in self.inotify.read():
                    if not name:              # open/close of a watched node
                        relevant = True
                    elif NODE_NAME.match(name):  # camera node added or removed
                        relevant = rescan = True
                if relevant:
                    deadline = time.monotonic() + self.debounce
            if deadline is not None and time.monotonic() >= deadline:
                deadline = None
                if rescan:
                    rescan = False
                    self._rescan()
                self.check()

    def close(self):
        self.inotify.close()
