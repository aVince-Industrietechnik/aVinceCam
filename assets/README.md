# Assets

Source files for icons and other resources.

- `icons/` – SVG sources. Panel icons are symbolic (single colour, recoloured by the
  theme) so that they work with light and dark Cinnamon themes.

`screenshots/en/`, `screenshots/de/` – screenshots for the README, taken with
the fake core (`tests/fake-core`, scenario `mixed`), so they show simulated
cameras only.

`scripts/build-icons.py` builds the applet icons (colourful and symbolic, plus
the variants shown while a preview runs) from these sources.
