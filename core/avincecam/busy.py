"""Detect whether camera device nodes are held open by a process.

Reads only /proc/<pid>/fd links. Processes of other users are not readable
for a normal user, so the result is "as far as visible" – callers report it
that way.
"""

import os

PROC = "/proc"


def _comm(pid, proc_root):
    try:
        with open(os.path.join(proc_root, pid, "comm"), encoding="utf-8",
                  errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def holders(device_paths, proc_root=PROC, exclude_pids=()):
    """Processes that have one of `device_paths` open.

    Returns a list of {"pid", "name", "device"}; one entry per process.
    """
    wanted = {os.path.realpath(p) for p in device_paths}
    exclude = {str(p) for p in exclude_pids}
    found = []
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return found
    for pid in pids:
        if pid in exclude:
            continue
        fd_dir = os.path.join(proc_root, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue  # other user's process or already gone
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target in wanted:
                found.append({"pid": int(pid), "name": _comm(pid, proc_root),
                              "device": target})
                break
    return found
