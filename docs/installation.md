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
