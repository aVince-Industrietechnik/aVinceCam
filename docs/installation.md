# Installation and removal

Not available yet. Planned for a later development phase:

- `scripts/install.sh` – user level only: copies the applet (including the
  core) to `~/.local/share/cinnamon/applets/avincecam@avince/`. No root rights,
  no autostart entries, no system packages. Existing files are backed up
  first, and every installed file is recorded in a list.
- `scripts/uninstall.sh` – removes exactly the files in that list, the applet
  settings in `~/.config/cinnamon/spices/avincecam@avince/` and the runtime
  directory. Remove the applet from the panel before running it.

The applet is added to the panel by the user via *System Settings → Applets*.

## Translations

Cinnamon reads applet translations from `~/.local/share/locale`. In the
applet folder:

- `cinnamon-xlet-makepot .` updates the template `po/avincecam@avince.pot`
- `cinnamon-xlet-makepot -i .` compiles `po/*.po` and installs the `.mo`
  files into `~/.local/share/locale/<lang>/LC_MESSAGES/`
- `cinnamon-xlet-makepot -r .` removes them again

Cinnamon loads new applet code and translations only after a restart
(Alt+F2, r, Enter on X11).
