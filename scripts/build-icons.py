#!/usr/bin/env python3
"""Copy the icons from assets/icons into the applet folder.

- assets/icons/avincecam.svg          -> applet/<uuid>/icon.png (128 px,
  shown in System Settings -> Applets and on Cinnamon Spices)
- assets/icons/avincecam.svg          -> applet/<uuid>/icons/ (colourful
  panel icon, the default)
- assets/icons/avincecam-symbolic.svg -> applet/<uuid>/icons/ (single-colour
  panel icon, recoloured by Cinnamon to match the theme)

Uses GdkPixbuf with the SVG loader that ships with every Cinnamon desktop.
"""

import os
import shutil
import sys

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ASSETS = os.path.join(REPO, "assets", "icons")
APPLET = os.path.join(REPO, "applet", "avincecam@avince")
SIZE = 128


def main():
    png = os.path.join(APPLET, "icon.png")
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
        os.path.join(ASSETS, "avincecam.svg"), SIZE, SIZE)
    pixbuf.savev(png, "png", [], [])

    icons = os.path.join(APPLET, "icons")
    os.makedirs(icons, exist_ok=True)
    # Panel icons stay SVG so they are sharp at every panel size.
    for name in ("avincecam.svg", "avincecam-symbolic.svg",
                 "avincecam-active.svg", "avincecam-active-symbolic.svg"):
        shutil.copyfile(os.path.join(ASSETS, name), os.path.join(icons, name))
    print(f"written: {os.path.relpath(png, REPO)} ({SIZE} px) and "
          f"{len(os.listdir(icons))} panel icons in applet/avincecam@avince/icons/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
