# Installation and removal

Everything happens in your home folder: no root rights, no autostart
entries, no system packages.

## Install

```
scripts/install.sh
```

- copies the applet together with its core to
  `~/.local/share/cinnamon/applets/avincecam@avince/`
- installs the translations to `~/.local/share/locale/<lang>/LC_MESSAGES/`
- backs up anything already there to `~/.local/share/avincecam/backups/<time>/`
- lists every installed path in `~/.local/share/avincecam/installed.txt`
- refuses to replace a development link (`scripts/dev-link.sh unlink` first)

Then add the applet via *System Settings → Applets*. After an update,
restart Cinnamon (Alt+F2, r, Enter on X11) – Cinnamon keeps the old applet
code in memory until then.

## Remove

1. Remove the applet from the panel: right-click on its icon → *Remove*.
2. Run

   ```
   scripts/uninstall.sh            # keeps your applet settings
   scripts/uninstall.sh --purge    # also removes the settings
   ```

`uninstall.sh` stops a running aVinceCam preview, removes exactly the paths
listed in `installed.txt` (never anything outside your home folder) and the
runtime folder `$XDG_RUNTIME_DIR/avincecam`. Backups of earlier
installations stay in `~/.local/share/avincecam/backups/`.

## Requirements

- Cinnamon 6.x, Python 3
- libcamera cameras: `libcamera-tools` (`cam`, `qcam`); GStreamer preview:
  `gstreamer1.0-libcamera`; virtual camera: additionally
  `gstreamer1.0-pipewire`, `python3-gi`, `gir1.2-gstreamer-1.0`
- USB cameras: `vlc` for the preview (recommended); otherwise
  `gstreamer1.0-tools` and `gstreamer1.0-plugins-good` (see D12)

## Translations

Cinnamon reads applet translations from `~/.local/share/locale`. In the
applet folder:

- `cinnamon-xlet-makepot .` updates the template `po/avincecam@avince.pot`
- `cinnamon-xlet-makepot -i .` compiles `po/*.po` and installs the `.mo`
  files into `~/.local/share/locale/<lang>/LC_MESSAGES/`
- `cinnamon-xlet-makepot -r .` removes them again

Cinnamon loads new applet code and translations only after a restart
(Alt+F2, r, Enter on X11).
