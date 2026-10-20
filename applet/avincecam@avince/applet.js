/*
 * aVinceCam – Camera Control for Linux
 * Cinnamon applet.
 *
 * The applet never opens a camera device itself. Everything that touches
 * hardware happens in the core (a separate, short-lived process):
 *
 *   menu opened         -> core "scan"   (sysfs only, no device access)
 *   "Query details"     -> core "probe"  (starts libcamera cameras briefly)
 *   "Start preview"     -> core "preview" (becomes the viewer)
 *   "Take photo"        -> core "snapshot" (streams ~1 s, saves a PNG)
 *   while loaded        -> core "watch"  (which programs use a camera;
 *                                         inotify and /proc, no device access)
 */

const Applet = imports.ui.applet;
const PopupMenu = imports.ui.popupMenu;
const Settings = imports.ui.settings;
const St = imports.gi.St;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const Pango = imports.gi.Pango;
const Clutter = imports.gi.Clutter;
const GObject = imports.gi.GObject;
const Gettext = imports.gettext;
const Main = imports.ui.main;
const MessageTray = imports.ui.messageTray;

const UUID = "avincecam@avince";
const AppletDir = imports.applets[UUID];
const Presenter = AppletDir.presenter.Presenter;
const CoreClient = AppletDir.coreClient;

const SCAN_TIMEOUT_SECONDS = 10;
const PROBE_TIMEOUT_SECONDS = 40;
const PREVIEW_START_TIMEOUT_SECONDS = 15;
const STOP_TIMEOUT_SECONDS = 15;
// cam needs ~1.5 s for 30 frames, the PNG a few seconds more.
const SNAPSHOT_TIMEOUT_SECONDS = 90;
// The watch process is started again after an unexpected end, a few times.
const WATCH_RETRY_SECONDS = 10;
const WATCH_RESTARTS = 5;
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
        this._previewBusy = false;  // a start or stop request is under way
        this._previewProc = null;   // viewer started by this applet instance
        this._snapshotBusy = false; // a photo is being taken
        this._usage = [];           // other programs using a camera (core "watch")
        this._watch = null;
        this._watchRetryId = 0;
        this._watchRestarts = 0;

        this._metadataPath = metadata.path;
        this.selectedCamera = "";
        this.panelIconStyle = "color";
        this.panelIconSizeColor = 28;
        this.panelIconSizeSymbolic = 24;
        this.previewViewer = "qcam";
        this.allowLensCameras = false;
        this.activeIndicator = "rotate";
        this.usageIndicator = true;
        this.snapshotSize = "1600x1200";
        this.snapshotFolder = "";
        this._indicatorKey = null;
        this.settings = new Settings.AppletSettings(this, metadata.uuid, instanceId);
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "preview-viewer", "previewViewer", null);
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "allow-lens-cameras", "allowLensCameras",
                                   () => this._buildMenu());
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "active-indicator", "activeIndicator",
                                   () => this._updateIndicator());
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "snapshot-size", "snapshotSize", null);
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "snapshot-folder", "snapshotFolder", null);
        this.settings.bindProperty(Settings.BindingDirection.IN,
                                   "usage-indicator", "usageIndicator",
                                   () => this._applyUsageSetting());
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
        this._relayoutId = this.menu.box.connect_after("queue-relayout",
                                                       () => this._dropCachedRowSizes(this.menu.box));
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
        this._applyUsageSetting();
    }

    on_applet_clicked(event) {
        this.menu.toggle();
    }

    // ------------------------------------------------------------ panel icon

    /* Colourful (default) or single-colour icon, optionally in an own size. */
    _applyPanelIcon() {
        const symbolic = this.panelIconStyle === "symbolic";
        // Red dot: own preview with "pulse", or another program uses a camera.
        const dot = (this._ownCameraActive() && this.activeIndicator === "pulse") ||
                    this._cameraInUse();
        const name = "avincecam" + (dot ? "-active" : "") + (symbolic ? "-symbolic" : "") + ".svg";
        const file = GLib.build_filenamev([this._metadataPath, "icons", name]);
        if (!GLib.file_test(file, GLib.FileTest.IS_REGULAR))
            this.set_applet_icon_symbolic_name(FALLBACK_ICON_NAME);
        else if (symbolic)
            this.set_applet_icon_symbolic_path(file);
        else
            this.set_applet_icon_path(file);
        this._applyIconSize();
        this._applyIndicatorAnimation();
    }

    /*
     * While aVinceCam itself uses a camera (preview, virtual camera, photo)
     * the icon turns slowly or pulses (setting "active-indicator"). Clutter
     * runs the animation itself; nothing runs when no camera is in use.
     */
    _applyIndicatorAnimation() {
        const icon = this._applet_icon;
        if (!icon)
            return;
        for (const name of ["rotation-angle-z", "scale-x", "scale-y"])
            icon.remove_transition("avincecam-" + name);
        icon.rotation_angle_z = 0;
        icon.set_scale(1, 1);
        if (this._removed || !this._ownCameraActive() || this.activeIndicator === "off")
            return;

        icon.set_pivot_point(0.5, 0.5);
        if (this.activeIndicator === "pulse") {
            for (const prop of ["scale-x", "scale-y"])
                this._loop(icon, prop, 1.0, 1.12, 800, true, Clutter.AnimationMode.EASE_IN_OUT_SINE);
        } else {
            this._loop(icon, "rotation-angle-z", 0, 360, 4000, false, Clutter.AnimationMode.LINEAR);
        }
    }

    _loop(actor, property, from, to, duration, autoReverse, mode) {
        const transition = new Clutter.PropertyTransition({
            property_name: property,
            interval: new Clutter.Interval({ value_type: GObject.TYPE_DOUBLE }),
            duration: duration,
            repeat_count: -1,
            auto_reverse: autoReverse,
            progress_mode: mode
        });
        transition.set_from(from);
        transition.set_to(to);
        actor.add_transition("avincecam-" + property, transition);
    }

    /* Re-apply the icon only when what it shows has changed. */
    _updateIndicator() {
        const key = [this._ownCameraActive(), this.activeIndicator, this.panelIconStyle,
                     this._cameraInUse()].join("|");
        if (key === this._indicatorKey)
            return;
        this._indicatorKey = key;
        this._applyPanelIcon();
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
        this._stopWatch();
        this._applyIndicatorAnimation();  // stops any running animation
        if (this._themeChangedId) {
            this._themeContext.disconnect(this._themeChangedId);
            this._themeChangedId = 0;
        }
        if (this._relayoutId) {
            this.menu.box.disconnect(this._relayoutId);
            this._relayoutId = 0;
        }
        // Do not leave a preview behind that nobody can stop from the panel.
        if (this._previewRunning() && this.client.commandPrefix) {
            try {
                Gio.Subprocess.new(this.client.commandPrefix.concat(["stop"]),
                                   Gio.SubprocessFlags.STDOUT_SILENCE |
                                   Gio.SubprocessFlags.STDERR_SILENCE);
            } catch (e) {
                global.logError("aVinceCam: could not stop the preview: " + e);
            }
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
        return JSON.stringify([this._cameras(), this._lastError, this.selectedCamera,
                               this._scan ? this._scan.preview : null]);
    }

    // ------------------------------------------------------------ preview

    /* Preview state as last reported by the core (lock file based). */
    _previewState() {
        return (this._scan && this._scan.preview) || { running: false };
    }

    /* aVinceCam itself uses a camera: preview, virtual camera or photo. */
    _ownCameraActive() {
        return this._previewRunning() || this._snapshotBusy;
    }

    _previewRunning() {
        return this._previewProc !== null || this._previewState().running === true;
    }

    /*
     * viewer: "qcam"/"gst" from the settings, or "virtual" to offer the
     * camera to other programs through PipeWire (browsers, video calls).
     */
    _startPreview(cameraId, viewer) {
        if (this._removed || this._previewBusy || this._previewRunning())
            return;
        this._previewBusy = true;
        this._lastError = null;
        const startedAt = GLib.get_monotonic_time();
        let proc = null;
        const args = ["preview", cameraId, "--viewer",
                      viewer || (this.previewViewer === "gst" ? "gst" : "qcam")];
        if (this.allowLensCameras)
            args.push("--allow-lens");
        proc = this.client.startPreview(
            args,
            PREVIEW_START_TIMEOUT_SECONDS,
            result => {
                this._previewBusy = false;
                const data = result.data || {};
                if (result.error || !data.started) {
                    this._previewProc = null;
                    this._lastError = { error: result.error ||
                        { code: "core-failed", message: null, detail: null }, context: "preview" };
                } else {
                    this._previewProc = proc;
                    if (this._scan)
                        this._scan.preview = { running: true, camera: data.camera,
                                               viewer: data.viewer, pid: data.pid, log: data.log };
                }
                this._buildMenu();
                this._updateTooltip();
            },
            (exitStatus, signaled) => {
                const seconds = (GLib.get_monotonic_time() - startedAt) / 1e6;
                const log = this._scan && this._scan.preview ? this._scan.preview.log : null;
                this._previewProc = null;
                this._previewBusy = false;
                if (this._scan)
                    this._scan.preview = { running: false };
                // A viewer that fails right away usually could not open the camera.
                if (!signaled && exitStatus !== 0 && seconds < 15)
                    this._reportViewerFailure(log);
                // The viewer ended on its own (window closed): remove its lock.
                // --cleanup-only never stops a process, so a preview started in
                // the meantime is safe.
                this.client.run(["stop", "--cleanup-only"], STOP_TIMEOUT_SECONDS, () => {});
                this._buildMenu();
                this._updateTooltip();
            });
        if (proc === null)
            this._previewBusy = false;
        this._buildMenu();
    }

    // ------------------------------------------------------------ photo

    /*
     * The core streams ~30 frames (exposure and noise reduction settle) and
     * saves the last one as PNG; ~1.5 s of camera use plus a few seconds.
     */
    _takePhoto(camera) {
        if (this._removed || this._snapshotBusy || this._previewBusy || this._previewRunning())
            return;
        this._snapshotBusy = true;
        this._lastError = null;
        const args = ["snapshot", camera.id, "--size", this.snapshotSize || "1600x1200"];
        const folder = this._snapshotFolderPath();
        if (folder)
            args.push("--out", folder);
        if (camera.has_lens && this.allowLensCameras)
            args.push("--allow-lens");
        this._buildMenu();
        this._updateIndicator();
        this.client.run(args, SNAPSHOT_TIMEOUT_SECONDS, result => {
            this._snapshotBusy = false;
            if (result.error || !result.data || !result.data.path) {
                this._lastError = { error: result.error ||
                    { code: "cam-failed", message: null, detail: null }, context: "snapshot" };
            } else {
                this._notifyPhoto(result.data.path, result.data.folder);
            }
            this._buildMenu();
            this._updateTooltip();
        });
    }

    /* The setting holds a path or a file:// URI; empty means the default. */
    _snapshotFolderPath() {
        const value = (this.snapshotFolder || "").trim();
        if (!value)
            return null;
        return value.startsWith("file://") ? Gio.File.new_for_uri(value).get_path() : value;
    }

    _notifyPhoto(path, folder) {
        const source = new MessageTray.SystemNotificationSource();
        Main.messageTray.add(source);
        const notification = new MessageTray.Notification(source, _("Photo saved"), path);
        notification.setTransient(false);
        notification.addButton("open-folder", _("Open folder"));
        notification.connect("action-invoked", () => {
            try {
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(folder).get_uri(), null);
            } catch (e) {
                global.logError("aVinceCam: could not open " + folder + ": " + e);
            }
        });
        source.notify(notification);
    }

    _reportViewerFailure(logPath) {
        this._lastError = { error: { code: "viewer-failed", message: null, detail: null },
                            context: "preview" };
        if (!logPath)
            return;
        Gio.File.new_for_path(logPath).load_contents_async(null, (file, res) => {
            try {
                const [, bytes] = file.load_contents_finish(res);
                const lines = new TextDecoder().decode(bytes).trim().split("\n");
                this._lastError.error.detail = lines.slice(-2).join(" / ");
                this._buildMenu();
            } catch (e) {
                // the log is optional
            }
        });
    }

    _stopPreview() {
        if (this._removed || this._previewBusy)
            return;
        this._previewBusy = true;
        this._buildMenu();
        this.client.run(["stop"], STOP_TIMEOUT_SECONDS, result => {
            this._previewBusy = false;
            if (result.error)
                this._lastError = { error: result.error, context: "stop" };
            if (this._scan)
                this._scan.preview = { running: false };
            this._buildMenu();
            this._updateTooltip();
            this._refresh();
        });
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
        const usage = Presenter.usageText(this._usage, cams, _);
        if (usage)
            text += "\n" + _("Camera in use: %s").replace("%s", usage);
        const running = this._previewState();
        if (running.running) {
            const cam = cams.find(c => c.id === running.camera);
            const label = running.viewer === "virtual-camera"
                ? _("Virtual camera active: %s") : _("Preview running: %s");
            text += "\n" + label.replace("%s",
                cam ? Presenter.cameraTitle(cam, _) : (running.camera || "?"));
        }
        this.set_applet_tooltip(text);
        // Called after every change of the preview state.
        this._updateIndicator();
    }

    // ------------------------------------------------------------ camera use

    /*
     * Show when another program uses a camera (setting "usage-indicator").
     * The core's "watch" command runs while the applet is loaded; it learns
     * about opened device nodes from inotify and reads the programs from
     * /proc, so it never opens a camera itself.
     */
    _applyUsageSetting() {
        if (this.usageIndicator && !this._removed)
            this._startWatch();
        else
            this._stopWatch();
    }

    _startWatch() {
        if (this._watch || this._watchRetryId)
            return;
        this._watch = this.client.watch(["watch"], result => this._onUsage(result), () => {
            // Ended unexpectedly: try again a few times, then give up quietly.
            this._watch = null;
            this._setUsage([]);
            if (this._removed || this._watchRestarts >= WATCH_RESTARTS)
                return;
            this._watchRestarts++;
            this._watchRetryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT,
                                                          WATCH_RETRY_SECONDS, () => {
                this._watchRetryId = 0;
                this._applyUsageSetting();
                return GLib.SOURCE_REMOVE;
            });
        });
    }

    _stopWatch() {
        if (this._watchRetryId) {
            GLib.source_remove(this._watchRetryId);
            this._watchRetryId = 0;
        }
        if (this._watch) {
            this._watch.stop();
            this._watch = null;
        }
        this._setUsage([]);
    }

    _onUsage(result) {
        if (!result.data || !Array.isArray(result.data.users))
            return;
        this._watchRestarts = 0;
        const users = Presenter.foreignUsers(result.data.users);
        // A camera plugged in after the last scan: scan again (sysfs only),
        // so the menu and tooltip can name it.
        const known = new Set(this._cameras().map(c => c.id));
        if (users.some(u => (u.cameras || []).some(id => !known.has(id))))
            this._refresh();
        this._setUsage(users);
    }

    _setUsage(users) {
        if (JSON.stringify(users) === JSON.stringify(this._usage))
            return;
        this._usage = users;
        if (this._removed)
            return;
        this._updateTooltip();  // also updates the panel icon
        if (this.menu.isOpen)
            this._buildMenu();
    }

    _cameraInUse() {
        return this._usage.length > 0;
    }

    // ------------------------------------------------------------ menu

    _buildMenu() {
        this.menu.removeAll();

        const header = new PopupMenu.PopupMenuItem(_("Cameras"), { reactive: false });
        header.label.add_style_class_name("avincecam-header");
        this.menu.addMenuItem(header);

        const usage = Presenter.usageText(this._usage, this._cameras(), _);
        if (usage)
            this.menu.addMenuItem(this._textItem(_("Camera in use: %s").replace("%s", usage),
                                                 "camera-web-symbolic", "avincecam-usage"));

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
        this._addPreviewItem(selected);

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
     * Cinnamon aligns all rows of a menu, submenus included, in shared
     * columns. Opening a submenu with longer rows widens the first column,
     * but the rows keep their cached preferred widths (Cinnamon only drops
     * them for themes with a background image, see
     * PopupMenuBase._menuQueueRelayout). For some frames the rows are then
     * narrower than their columns and Clutter logs "Actor 'StBin' tried to
     * allocate a size of -N x ..." for the ornament and the submenu arrow
     * (measured 2026-10-20). Dropping the caches on every relayout of the
     * menu keeps rows and columns in step.
     */
    _dropCachedRowSizes(box) {
        for (const child of box.get_children()) {
            child.queue_relayout();
            if (child._delegate instanceof PopupMenu.PopupSubMenu)
                this._dropCachedRowSizes(child._delegate.box);
        }
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

    /*
     * One entry: "Start preview" for the selected camera, or "Stop preview"
     * while one runs. Only one preview at a time (Intel IPU3 rule).
     */
    _addPreviewItem(selected) {
        const state = this._previewState();
        if (this._previewBusy) {
            this.menu.addMenuItem(this._textItem(_("Please wait …"), "content-loading-symbolic",
                                                 "avincecam-info"));
            return;
        }
        if (this._snapshotBusy || state.viewer === "snapshot") {
            this.menu.addMenuItem(this._textItem(_("Taking photo …"), "content-loading-symbolic",
                                                 "avincecam-info"));
            return;
        }
        if (this._previewRunning()) {
            const cam = this._cameras().find(c => c.id === state.camera);
            const title = cam ? Presenter.cameraTitle(cam, _) : "";
            const virtual = state.viewer === "virtual-camera";
            const text = virtual
                ? (title ? _("Stop virtual camera (%s)").replace("%s", title) : _("Stop virtual camera"))
                : (title ? _("Stop preview (%s)").replace("%s", title) : _("Stop preview"));
            const stop = new PopupMenu.PopupIconMenuItem(
                text, "media-playback-stop-symbolic", St.IconType.SYMBOLIC);
            stop.connect("activate", () => this._stopPreview());
            this.menu.addMenuItem(stop);
            if (virtual)
                this._addVirtualHints(cam);
            return;
        }
        if (!selected)
            return;
        if (selected.has_lens && !this.allowLensCameras) {
            // Locked: libcamera would drive the focus motor automatically.
            this.menu.addMenuItem(this._textItem(_("Preview locked: camera has a focus motor"),
                                                 "changes-prevent-symbolic", "avincecam-info"));
            return;
        }
        const start = new PopupMenu.PopupIconMenuItem(
            _("Start preview (%s)").replace("%s", Presenter.cameraTitle(selected, _)),
            "camera-video-symbolic", St.IconType.SYMBOLIC);
        start.connect("activate", () => this._startPreview(selected.id));
        this.menu.addMenuItem(start);

        // USB cameras reach other programs directly; libcamera cameras need
        // aVinceCam to offer them through PipeWire.
        if (selected.api === "libcamera") {
            const photo = new PopupMenu.PopupIconMenuItem(
                _("Take photo (%s)").replace("%s", Presenter.cameraTitle(selected, _)),
                "camera-photo-symbolic", St.IconType.SYMBOLIC);
            photo.connect("activate", () => this._takePhoto(selected));
            this.menu.addMenuItem(photo);

            const virtual = new PopupMenu.PopupIconMenuItem(
                _("Offer as virtual camera (%s)").replace("%s", Presenter.cameraTitle(selected, _)),
                "camera-web-symbolic", St.IconType.SYMBOLIC);
            virtual.connect("activate", () => this._startPreview(selected.id, "virtual"));
            this.menu.addMenuItem(virtual);
        }
    }

    /*
     * How to use the virtual camera, shown while it runs (desk test T7,
     * 2026-10-20): browsers need PipeWire camera support switched on, and
     * libcamera keeps USB cameras open, so they cannot be used meanwhile.
     */
    _addVirtualHints(cam) {
        const sub = new PopupMenu.PopupSubMenuMenuItem(_("Using the virtual camera"));
        const color = Presenter.noteColor(this._menuTextColor());
        const lines = [
            _("Choose \"aVinceCam: %s\" in the browser or call")
                .replace("%s", cam ? cam.label : "…"),
            _("Firefox: about:config, media.webrtc.camera.allow-pipewire = true, restart"),
            _("Chrome: chrome://flags, enable PipeWire camera support, restart"),
            _("USB cameras cannot be used by other programs meanwhile")
        ];
        for (const line of lines) {
            const item = this._textItem(line, "dialog-information-symbolic", "avincecam-note");
            item.actor.set_style("color: " + color + ";");
            sub.menu.addMenuItem(item);
        }
        this.menu.addMenuItem(sub);
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
        } else {
            // libcamera starts every camera of the machine for a query; a
            // USB camera is only asked for its list of formats.
            const query = new PopupMenu.PopupMenuItem(cam.api === "libcamera"
                ? _("Query sizes and frame rates (switches the cameras on briefly)")
                : _("Query sizes and frame rates"));
            query.connect("activate", () => this._probe(cam.id));
            query.setSensitive(this._probing === null);
            sub.menu.addMenuItem(query);
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
