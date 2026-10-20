"""Run GStreamer with only the plugins a pipeline needs.

GStreamer's video4linux2 plugin opens every video and sub-device node of the
machine when it is loaded (decision D12) – on an Intel IPU3 laptop that
includes the lens motor and powers up the camera pipeline. A process that
only needs libcamera therefore gets a private plugin folder with links to
exactly the plugins it uses, and a private registry, so the video4linux2
plugin is never loaded. Nothing outside aVinceCam's runtime folder changes.
"""

import os

PLUGIN_DIRS = ("/usr/lib/x86_64-linux-gnu/gstreamer-1.0",
               "/usr/lib/aarch64-linux-gnu/gstreamer-1.0",
               "/usr/lib64/gstreamer-1.0", "/usr/lib/gstreamer-1.0")
# Test hook: a folder with stand-in plugin files.
SYSTEM_DIR_ENV = "AVINCECAM_GST_SYSTEM_DIR"

PACKAGES = {"libgstlibcamera.so": "gstreamer1.0-libcamera",
            "libgstpipewire.so": "gstreamer1.0-pipewire"}
DEFAULT_PACKAGE = "gstreamer1.0-plugins-base"


class IsolationError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def find_plugin_dir(candidates=PLUGIN_DIRS):
    override = os.environ.get(SYSTEM_DIR_ENV)
    if override:
        candidates = (override,)
    for directory in candidates:
        if os.path.isfile(os.path.join(directory, "libgstlibcamera.so")):
            return directory
    return None


def prepare_plugins(private_dir, system_dir, required, optional=(), need_one_of=()):
    """Link exactly the wanted plugins into `private_dir`. Returns the list.

    `required` must all exist, `optional` are linked when present, and of
    `need_one_of` (e.g. video sinks) at least one must exist.
    """
    wanted = [p for p in required]
    missing = [p for p in required if not os.path.isfile(os.path.join(system_dir, p))]
    if missing:
        package = next((PACKAGES[p] for p in missing if p in PACKAGES), DEFAULT_PACKAGE)
        raise IsolationError("plugin-missing", "a GStreamer plugin is missing",
                             f"{', '.join(missing)} (package {package})")
    present = [p for p in tuple(optional) + tuple(need_one_of)
               if os.path.isfile(os.path.join(system_dir, p))]
    if need_one_of and not any(p in present for p in need_one_of):
        raise IsolationError("plugin-missing", "no GStreamer video output is installed",
                             f"one of {', '.join(need_one_of)} ({DEFAULT_PACKAGE})")
    wanted += [p for p in present if p not in wanted]

    os.makedirs(private_dir, mode=0o700, exist_ok=True)
    for name in os.listdir(private_dir):
        if name not in wanted:
            os.remove(os.path.join(private_dir, name))
    for name in wanted:
        link = os.path.join(private_dir, name)
        target = os.path.join(system_dir, name)
        if os.path.islink(link) and os.readlink(link) == target:
            continue
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(target, link)
    return sorted(os.listdir(private_dir))


def environment(private_dir, registry):
    """Environment that limits GStreamer to the private plugin folder."""
    return {
        "GST_PLUGIN_SYSTEM_PATH_1_0": private_dir,
        "GST_PLUGIN_SYSTEM_PATH": private_dir,
        "GST_PLUGIN_PATH_1_0": "",
        "GST_PLUGIN_PATH": "",
        "GST_REGISTRY_1_0": registry,
        "GST_REGISTRY": registry,
    }


def isolated_environment(runtime_dir, name, required, optional=(), need_one_of=()):
    """Prepare `<runtime_dir>/<name>-plugins` and return the environment."""
    system_dir = find_plugin_dir()
    if system_dir is None:
        raise IsolationError("plugin-missing", "the GStreamer libcamera plugin was not found",
                             PACKAGES["libgstlibcamera.so"])
    private_dir = os.path.join(runtime_dir, name + "-plugins")
    prepare_plugins(private_dir, system_dir, required, optional, need_one_of)
    return environment(private_dir, os.path.join(runtime_dir, name + "-registry.bin"))
