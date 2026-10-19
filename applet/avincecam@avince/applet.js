/*
 * aVinceCam – Camera Control for Linux
 * Cinnamon applet.
 *
 * The applet never opens a camera device itself. Everything that touches
 * hardware happens in the core (a separate, short-lived process):
 *
 *   menu opened         -> core "scan"   (sysfs only, no device access)
 *   "Query details"     -> core "probe"  (starts libcamera cameras briefly)
 *
 * The preview follows in a later version.
 */

const Applet = imports.ui.applet;
const PopupMenu = imports.ui.popupMenu;
const Settings = imports.ui.settings;
const St = imports.gi.St;
const GLib = imports.gi.GLib;
const Pango = imports.gi.Pango;
const Gettext = imports.gettext;

const UUID = "avincecam@avince";
const AppletDir = imports.applets[UUID];
const Presenter = AppletDir.presenter.Presenter;
const CoreClient = AppletDir.coreClient;

const SCAN_TIMEOUT_SECONDS = 10;
const PROBE_TIMEOUT_SECONDS = 40;
// Used when the applet's own panel icon is missing.
const FALLBACK_ICON_NAME = "camera-web-symbolic";

Gettext.bindtextdomain(UUID, GLib.get_home_dir() + "/.local/share/locale");

function _(text) {
    return Gettext.dgettext(UUID, text);
}


class AVinceCamApplet extends Applet.IconApplet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);

        this._removed = false;
        this._scan = null;          // last scan answer
        this._details = {};         // camera id -> camera object from probe
        this._lastError = null;     // { error, context }
        this._probing = null;       // camera id being probed
        this._scanRunning = false;
        this._detailsOpen = false;  // keep the details submenu open on rebuild

        this._metadataPath = metadata.path;
        this.selectedCamera = "";
        this.panelIconStyle = "color";
        this.panelIconSizeColor = 28;
        this.panelIconSizeSymbolic = 24;
        this.settings = new Settings.AppletSettings(this, metadata.uuid, instanceId);
        this.settings.bindProperty(Settings.BindingDirection.BIDIRECTIONAL,
                                   "selected-camera", "selectedCamera", null);
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "panel-icon-style", "panelIconStyle",
                                   () => this._applyPanelIcon());
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "panel-icon-size-color", "panelIconSizeColor",
                                   () => this._applyPanelIcon());
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "panel-icon-size-symbolic", "panelIconSizeSymbolic",
                                   () => this._applyPanelIcon());

        this.client = new CoreClient.CoreClient(CoreClient.findCore(metadata.path));

        this._applyPanelIcon();
        this.set_applet_tooltip("aVinceCam");

        this.menuManager = new PopupMenu.PopupMenuManager(this);
        this.menu = new Applet.AppletPopupMenu(this, orientation);
        this.menuManager.addMenu(this.menu);
        this.menu.connect("open-state-changed", (menu, open) => {
            if (open)
                this._refresh();
            else
                this._detailsOpen = false;
        });

        // Note colours depend on the theme: rebuild when it changes.
        this._themeContext = St.ThemeContext.get_for_stage(global.stage);
        this._themeChangedId = this._themeContext.connect("changed", () => this._buildMenu());

        this._buildMenu();
        // First scan right away (sysfs only, no device access), so the menu
        // already has its content when it is opened for the first time.
        this._refresh();
    }

    on_applet_clicked(event) {
        this.menu.toggle();
    }

    // ------------------------------------------------------------ panel icon

    /* Colourful (default) or single-colour icon, optionally in an own size. */
    _applyPanelIcon() {
        const symbolic = this.panelIconStyle === "symbolic";
        const file = GLib.build_filenamev([this._metadataPath, "icons",
            symbolic ? "avincecam-symbolic.svg" : "avincecam.svg"]);
        if (!GLib.file_test(file, GLib.FileTest.IS_REGULAR))
            this.set_applet_icon_symbolic_name(FALLBACK_ICON_NAME);
        else if (symbolic)
            this.set_applet_icon_symbolic_path(file);
        else
            this.set_applet_icon_path(file);
        this._applyIconSize();
    }

    /*
     * Cinnamon resets the size when the panel changes; set ours again.
     * The icon never gets taller than the panel (minus a small margin).
     */
    _applyIconSize() {
        const wanted = Number(this.panelIconStyle === "symbolic"
                              ? this.panelIconSizeSymbolic
                              : this.panelIconSizeColor) || 0;
        if (!this._applet_icon || wanted <= 0)
            return;
        const panelHeight = this.panel ? this.panel.height : 0;
        const limit = panelHeight > 0 ? panelHeight - 4 : wanted;
        this._applet_icon.set_icon_size(Math.max(16, Math.min(wanted, limit)));
    }

    on_panel_icon_size_changed(size) {
        this._applyIconSize();
    }

    on_panel_height_changed() {
        this._applyIconSize();
    }

    on_applet_removed_from_panel() {
        this._removed = true;
        if (this._themeChangedId) {
            this._themeContext.disconnect(this._themeChangedId);
            this._themeChangedId = 0;
        }
        this.client.destroy();
        this.settings.finalize();
    }

    // ------------------------------------------------------------ data

    /*
     * Scan again. The menu is rebuilt only when the answer differs from the
     * one shown: replacing menu items while the menu opens makes Clutter log
     * layout warnings and would close an open submenu.
     */
    _refresh() {
        if (this._removed || this._scanRunning)
            return;
        this._scanRunning = true;
        this.client.run(["scan"], SCAN_TIMEOUT_SECONDS, result => {
            this._scanRunning = false;
            const before = this._stateKey();
            if (result.data && Array.isArray(result.data.cameras)) {
                this._scan = result.data;
                this._forgetVanishedDetails();
            }
            this._lastError = result.ok ? null : { error: result.error, context: "scan" };
            this._ensureSelection();
            if (this._stateKey() !== before)
                this._buildMenu();
            this._updateTooltip();
        });
    }

    _stateKey() {
        return JSON.stringify([this._cameras(), this._lastError, this.selectedCamera]);
    }

    _probe(cameraId) {
        if (this._removed || this._probing)
            return;
        this._probing = cameraId;
        this._detailsOpen = true;
        this._buildMenu();
        this.client.run(["probe", cameraId], PROBE_TIMEOUT_SECONDS, result => {
            this._probing = null;
            const cams = (result.data && result.data.cameras) || [];
            for (const cam of cams)
                this._details[cam.id] = cam;
            this._lastError = result.error ? { error: result.error, context: "probe" } : null;
            this._buildMenu();
        });
    }

    _cameras() {
        return (this._scan && this._scan.cameras) || [];
    }

    _forgetVanishedDetails() {
        const present = new Set(this._cameras().map(c => c.id));
        for (const id of Object.keys(this._details)) {
            if (!present.has(id))
                delete this._details[id];
        }
    }

    _ensureSelection() {
        const cams = this._cameras();
        if (cams.length === 0)
            return;
        if (!cams.some(c => c.id === this.selectedCamera))
            this.selectedCamera = cams[0].id;
    }

    _select(cameraId) {
        this.selectedCamera = cameraId;
        this._buildMenu();
        this._updateTooltip();
    }

    _updateTooltip() {
        const cams = this._cameras();
        const selected = cams.find(c => c.id === this.selectedCamera);
        let text = "aVinceCam";
        if (this._scan)
            text += "\n" + (cams.length === 1 ? _("1 camera")
                                               : _("%d cameras").replace("%d", String(cams.length)));
        if (selected)
            text += "\n" + _("Selected: %s").replace("%s", Presenter.cameraTitle(selected, _));
        this.set_applet_tooltip(text);
    }

    // ------------------------------------------------------------ menu

    _buildMenu() {
        this.menu.removeAll();

        const header = new PopupMenu.PopupMenuItem(_("Cameras"), { reactive: false });
        header.label.add_style_class_name("avincecam-header");
        this.menu.addMenuItem(header);

        if (!this._scan && !this._lastError) {
            this._addInfo(_("Looking for cameras …"));
        } else if (this._cameras().length === 0 && this._scan) {
            this._addInfo(_("No camera found."));
        }

        for (const cam of this._cameras())
            this._addCameraItem(cam);

        if (this._lastError)
            this._addError(this._lastError.error);

        const selected = this._cameras().find(c => c.id === this.selectedCamera);
        if (selected) {
            this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            this._addDetails(this._details[selected.id] || selected);
            this._addNotes(this._details[selected.id] || selected);
        }

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const refresh = new PopupMenu.PopupIconMenuItem(_("Search again"),
                                                        "view-refresh-symbolic",
                                                        St.IconType.SYMBOLIC);
        refresh.connect("activate", () => {
            this._details = {};
            this._refresh();
        });
        this.menu.addMenuItem(refresh);

        const copy = new PopupMenu.PopupIconMenuItem(_("Copy camera report"),
                                                     "edit-copy-symbolic",
                                                     St.IconType.SYMBOLIC);
        copy.connect("activate", () => this._copyReport());
        copy.setSensitive(this._scan !== null);
        this.menu.addMenuItem(copy);
    }

    /*
     * A text line that is easy to read but does nothing when clicked.
     * Cinnamon draws items created with reactive: false greyed out as
     * "insensitive", so the item stays sensitive, gets no hover highlight
     * and its activate() does nothing (the menu stays open).
     * No line wrapping: Cinnamon's menu then computes far too large heights
     * (tested 2026-10-19). Texts are kept short enough for one line instead.
     */
    _textItem(text, iconName, styleClass) {
        const params = { hover: false };
        const item = iconName
            ? new PopupMenu.PopupIconMenuItem(text, iconName, St.IconType.SYMBOLIC, params)
            : new PopupMenu.PopupMenuItem(text, params);
        item.activate = () => {};
        item.label.add_style_class_name(styleClass);
        item.label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        return item;
    }

    _addInfo(text) {
        this.menu.addMenuItem(this._textItem(text, null, "avincecam-info"));
    }

    _addError(error) {
        this.menu.addMenuItem(this._textItem(Presenter.errorText(error, _),
                                             "dialog-warning-symbolic", "avincecam-error"));
    }

    _addCameraItem(cam) {
        const subtitle = Presenter.cameraSubtitle(cam, _);
        const text = Presenter.cameraTitle(cam, _) + (subtitle ? "  —  " + subtitle : "");
        const item = new PopupMenu.PopupMenuItem(text);
        item.label.add_style_class_name("avincecam-camera");
        // Radio button: filled for the selected camera.
        item.setOrnament(PopupMenu.OrnamentType.DOT, cam.id === this.selectedCamera);
        item.connect("activate", () => this._select(cam.id));
        this.menu.addMenuItem(item);
    }

    _addDetails(cam) {
        const sub = new PopupMenu.PopupSubMenuMenuItem(
            _("Details: %s").replace("%s", Presenter.cameraTitle(cam, _)));
        for (const line of Presenter.detailLines(cam, _))
            sub.menu.addMenuItem(this._textItem(line, null, "avincecam-detail"));

        if (this._probing === cam.id) {
            sub.menu.addMenuItem(this._textItem(_("Querying details …"), null, "avincecam-detail"));
        } else if (cam.api === "libcamera") {
            const query = new PopupMenu.PopupMenuItem(
                _("Query sizes and frame rates (switches the cameras on briefly)"));
            query.connect("activate", () => this._probe(cam.id));
            query.setSensitive(this._probing === null);
            sub.menu.addMenuItem(query);
        } else {
            sub.menu.addMenuItem(this._textItem(
                _("Details for USB cameras are not available yet."), null, "avincecam-detail"));
        }
        this.menu.addMenuItem(sub);
        // Rebuilding the menu would close the submenu the user is working in.
        if (this._detailsOpen)
            sub.menu.open(false);
    }

    _addNotes(cam) {
        const warnings = cam.warnings || [];
        if (warnings.length === 0)
            return;
        const sub = new PopupMenu.PopupSubMenuMenuItem(
            _("Notes (%d)").replace("%d", String(warnings.length)));
        const color = Presenter.noteColor(this._menuTextColor());
        for (const code of warnings) {
            const item = this._textItem(Presenter.warningText(code, _),
                                        "dialog-information-symbolic", "avincecam-note");
            // On the item, so text and icon share the colour.
            item.actor.set_style("color: " + color + ";");
            sub.menu.addMenuItem(item);
        }
        this.menu.addMenuItem(sub);
    }

    /* Text colour of the menu in the current theme as [r, g, b], or null. */
    _menuTextColor() {
        try {
            const c = this.menu.actor.get_theme_node().get_foreground_color();
            return [c.red, c.green, c.blue];
        } catch (e) {
            return null;
        }
    }

    _copyReport() {
        const text = Presenter.summaryText(this._scan, this._details, _);
        St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new AVinceCamApplet(metadata, orientation, panelHeight, instanceId);
}
