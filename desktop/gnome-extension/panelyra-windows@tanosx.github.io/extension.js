import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {WindowRestore} from './windowRestore.js';

const OBJECT_PATH = '/io/github/tanosx/Panelyra/Windows';
const INTERFACE = 'io.github.tanosx.Panelyra.Windows1';
const XML = `<node><interface name="${INTERFACE}">
  <property name="Version" type="u" access="read"/>
  <method name="Register"><arg name="connector" type="s" direction="in"/><arg name="registered" type="b" direction="out"/></method>
  <method name="Restore"><arg name="connector" type="s" direction="in"/><arg name="restored_count" type="u" direction="out"/></method>
  <method name="Unregister"/>
</interface></node>`;

export default class PanelyraWindowsExtension extends Extension {
    enable() {
        // Headless/non-GDM Shell sessions can have no screen shield. Never
        // advertise a usable D-Bus service unless the pre-lock hook is available.
        if (typeof Main.screenShield?.activate !== 'function')
            throw new Error('Panelyra Window Restore requires the GNOME screen shield');
        try {
            this._enable();
        } catch (error) {
            // Shell does not guarantee disable() after a failed enable().
            this.disable();
            throw error;
        }
    }

    _enable() {
        this._ownerWatch = 0;
        this._engine = new WindowRestore({
            isLocked: () => Main.screenShield.locked || Main.screenShield.active || Main.sessionMode.isLocked,
            findMonitor: connector => {
                const index = global.backend.get_monitor_manager().get_monitor_for_connector(connector);
                if (index < 0)
                    return null;
                const geometry = global.display.get_monitor_geometry(index);
                return {
                    index, x: geometry.x, y: geometry.y,
                    width: geometry.width, height: geometry.height,
                    scale: global.display.get_monitor_scale(index),
                };
            },
            listWindows: () => global.display.list_all_windows(),
            isEligible: window => [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG,
                Meta.WindowType.MODAL_DIALOG].includes(window.get_window_type()),
            keepWorkspaceAlive: (workspace, duration) => Main.wm.keepWorkspaceAlive(workspace, duration),
            resolveWorkspace: (saved, index) => {
                const manager = global.workspace_manager;
                for (let i = 0; i < manager.n_workspaces; i++) {
                    const workspace = manager.get_workspace_by_index(i);
                    if (workspace === saved)
                        return workspace;
                }
                // Meta caps the workspace count; protect against stale/invalid
                // indices as well. Never change the user's workspace preference.
                if (!Number.isInteger(index) || index < 0 || index >= 36)
                    return null;
                while (manager.n_workspaces <= index) {
                    const count = manager.n_workspaces;
                    const workspace = manager.append_new_workspace(false, global.get_current_time());
                    if (!workspace || manager.n_workspaces <= count)
                        return null;
                    Main.wm.keepWorkspaceAlive(workspace, 5 * 60 * 1000);
                }
                return manager.get_workspace_by_index(index);
            },
            setTimeout: (callback, duration) => GLib.timeout_add(GLib.PRIORITY_DEFAULT, duration, () => {
                callback();
                return GLib.SOURCE_REMOVE;
            }),
            clearTimeout: id => GLib.Source.remove(id),
            logError: error => console.error(`[Panelyra Window Restore] ${error}`),
        });
        this._injections = new InjectionManager();
        const extension = this;
        this._injections.overrideMethod(Main.screenShield, 'activate', original => function (...args) {
            try {
                extension._engine?.captureBeforeLock();
            } catch (error) {
                console.error(`[Panelyra Window Restore] Snapshot failed: ${error}`);
            }
            // Security-critical: locking must continue even if snapshotting fails.
            return original.apply(this, args);
        });
        // Publish only after the snapshot hook has been installed successfully.
        this._dbus = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
    }

    get Version() {
        return 1;
    }

    RegisterAsync([connector], invocation) {
        this._reply(invocation, '(b)', () => {
            const sender = invocation.get_sender();
            const registered = this._engine.register(sender, connector);
            if (registered && !this._ownerWatch) {
                this._ownerWatch = Gio.bus_watch_name_on_connection(
                    Gio.DBus.session, sender, Gio.BusNameWatcherFlags.NONE,
                    null, () => this._clearOwner());
            }
            return [registered];
        });
    }

    RestoreAsync([connector], invocation) {
        this._reply(invocation, '(u)', async () => [
            await this._engine.restore(invocation.get_sender(), connector),
        ]);
    }

    UnregisterAsync(_args, invocation) {
        this._reply(invocation, '()', () => {
            this._engine.unregister(invocation.get_sender());
            this._clearOwner();
            return [];
        });
    }

    async _reply(invocation, signature, operation) {
        try {
            const values = await operation();
            invocation.return_value(new GLib.Variant(signature, values));
        } catch (error) {
            const code = error.code ?? 'Failed';
            invocation.return_dbus_error(`${INTERFACE}.${code}`, error.message);
        }
    }

    _clearOwner() {
        const watch = this._ownerWatch;
        this._ownerWatch = 0;
        try {
            if (watch)
                Gio.bus_unwatch_name(watch);
        } finally {
            this._engine?.clear();
        }
    }

    disable() {
        const dbus = this._dbus;
        const injections = this._injections;
        this._dbus = null;
        this._injections = null;
        // A cleanup failure must not strand the other resources, in particular
        // the exported capability or a wrapper around the lock operation.
        for (const cleanup of [() => dbus?.unexport(), () => injections?.clear(), () => this._clearOwner()]) {
            try {
                cleanup();
            } catch (error) {
                console.error(`[Panelyra Window Restore] Cleanup failed: ${error}`);
            }
        }
        this._engine = null;
    }
}
