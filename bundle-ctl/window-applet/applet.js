const Applet = imports.ui.applet;
const ByteArray = imports.byteArray;
const GLib = imports.gi.GLib;
const Util = imports.misc.util;

const AGENT = "bundle-ctl/terminator-";
const CHECK = 2;

class BundleWindows extends Applet.TextApplet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        const linked = GLib.file_test(metadata.path, GLib.FileTest.IS_SYMLINK);
        const folder = linked ? GLib.file_read_link(metadata.path) : metadata.path;
        this.picker = GLib.build_filenamev([GLib.path_get_dirname(folder), "window-picker.py"]);
        this.set_applet_label("✋");
        this.minimized = global.window_manager.connect("minimize", (wm, actor) =>
            this.place(actor.get_meta_window())
        );
        this.update();
        this.timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, CHECK, () => {
            this.update();
            return GLib.SOURCE_CONTINUE;
        });
    }

    isAgent(window) {
        try {
            const [, bytes] = GLib.file_get_contents(`/proc/${window.get_pid()}/cmdline`);
            return ByteArray.toString(bytes.map((byte) => byte || 32)).includes(AGENT);
        } catch {
            return false;
        }
    }

    place(window) {
        if (window.minimized === window.is_skip_taskbar() || !this.isAgent(window)) {
            return;
        }
        const action = `${window.minimized ? "add" : "remove"},skip_taskbar`;
        const pid = String(window.get_pid());
        Util.spawnCommandLineAsyncIO("wmctrl -l -p", (stdout) => {
            for (const [id, , owner] of stdout.split("\n").map((line) => line.split(/\s+/))) {
                if (owner === pid) {
                    Util.spawn(["wmctrl", "-i", "-r", id, "-b", action]);
                }
            }
        });
    }

    update() {
        global.get_window_actors().forEach((actor) => this.place(actor.get_meta_window()));
        Util.spawnCommandLineAsyncIO(`${GLib.shell_quote(this.picker)} --counts`, (stdout) => {
            const [label, tooltip] = stdout.split("\n");
            if (label) {
                this.set_applet_label(label);
                this.set_applet_tooltip(tooltip);
            }
        });
    }

    on_applet_clicked() {
        Util.spawn(["rofi", "-modi", `windows:${this.picker}`, "-show", "windows"]);
    }

    on_applet_removed_from_panel() {
        GLib.source_remove(this.timer);
        global.window_manager.disconnect(this.minimized);
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new BundleWindows(metadata, orientation, panelHeight, instanceId);
}
