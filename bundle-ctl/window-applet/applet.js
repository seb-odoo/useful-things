const Applet = imports.ui.applet;
const GLib = imports.gi.GLib;
const Util = imports.misc.util;

const CHECK = 2;

class BundleWindows extends Applet.TextApplet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        const linked = GLib.file_test(metadata.path, GLib.FileTest.IS_SYMLINK);
        const folder = linked ? GLib.file_read_link(metadata.path) : metadata.path;
        this.picker = GLib.build_filenamev([GLib.path_get_dirname(folder), "window-picker.py"]);
        this.set_applet_label("✋");
        this.update();
        this.timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, CHECK, () => {
            this.update();
            return GLib.SOURCE_CONTINUE;
        });
    }

    update() {
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
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new BundleWindows(metadata, orientation, panelHeight, instanceId);
}
