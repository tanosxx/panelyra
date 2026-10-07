// This module deliberately has no Shell/GI imports, so the actual restoration
// policy can be exercised with fake windows without locking a running desktop.
const CONNECTOR = /^Meta-[0-9]+$/;
const WORKSPACE_KEEP_ALIVE_MS = 5 * 60 * 1000;
const RESTORE_DELAYS_MS = [0, 150, 350, 550];
const RECT_TOLERANCE = 2;

function copyRect(rect) {
    return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
}

function compatibleMonitor(saved, current) {
    return saved.width === current.width && saved.height === current.height &&
        Math.abs(saved.scale - current.scale) < 0.001;
}

function equalRect(actual, expected, maximized) {
    const keys = [];
    // Mutter's GNOME 50 MaximizeFlags: HORIZONTAL=1, VERTICAL=2.
    if (!(maximized & 1))
        keys.push('x', 'width');
    if (!(maximized & 2))
        keys.push('y', 'height');
    return keys.every(key =>
        Math.abs(actual[key] - expected[key]) <= RECT_TOLERANCE);
}

export class WindowRestoreError extends Error {
    constructor(code, message) {
        super(message);
        this.code = code;
    }
}

export class WindowRestore {
    constructor(environment) {
        this._env = environment;
        this.owner = null;
        this.connector = null;
        this._snapshot = null;
        this._generation = 0;
        this._waits = new Map();
        this._restoring = null;
    }

    _checkOwner(owner) {
        if (this.owner && this.owner !== owner)
            throw new WindowRestoreError('NotOwner', 'Another Panelyra connection owns this registration');
    }

    _checkConnector(connector) {
        if (!CONNECTOR.test(connector))
            throw new WindowRestoreError('InvalidConnector', 'Only a Mutter virtual monitor can be registered');
    }

    register(owner, connector) {
        this._checkOwner(owner);
        this._checkConnector(connector);
        if (!owner?.startsWith(':'))
            throw new WindowRestoreError('InvalidOwner', 'A unique D-Bus sender is required');
        if (this._env.isLocked() || !this._env.findMonitor(connector))
            return false;
        this.owner = owner;
        this.connector = connector;
        // Reconnecting can allocate another Meta-N connector. Its registration
        // must not overwrite the snapshot taken before the original disappeared.
        return true;
    }

    unregister(owner) {
        this._checkOwner(owner);
        this.clear();
    }

    clear() {
        this._generation++;
        this.owner = null;
        this.connector = null;
        this._snapshot = null;
        for (const [id, resolve] of this._waits) {
            this._env.clearTimeout(id);
            resolve(false);
        }
        this._waits.clear();
        this._restoring = null;
    }

    captureBeforeLock() {
        if (!this.owner || this._snapshot || this._env.isLocked())
            return;
        const monitor = this._env.findMonitor(this.connector);
        if (!monitor)
            return;
        const windows = this._env.listWindows();
        const selected = new Set();
        for (const window of windows) {
            try {
                if (this._env.isEligible(window) && window.get_monitor() === monitor.index)
                    selected.add(window);
            } catch (error) {
                this._env.logError(error);
            }
        }
        // Include nested transient dialogs even when Mutter has put a dialog on
        // another monitor. Never pull in an unrelated application window.
        for (let pass = 0; pass < windows.length; pass++) {
            let added = false;
            for (const window of windows) {
                try {
                    if (!selected.has(window) && this._env.isEligible(window) &&
                        selected.has(window.get_transient_for())) {
                        selected.add(window);
                        added = true;
                    }
                } catch (error) {
                    this._env.logError(error);
                }
            }
            if (!added)
                break;
        }
        const saved = [];
        for (const window of selected) {
            try {
                const frame = copyRect(window.get_frame_rect());
                const workspace = window.get_workspace();
                if (!workspace || frame.width <= 0 || frame.height <= 0)
                    continue;
                const ancestors = new Set([window]);
                let parent = window.get_transient_for();
                while (parent && selected.has(parent) && !ancestors.has(parent)) {
                    ancestors.add(parent);
                    parent = parent.get_transient_for();
                }
                saved.push({
                    window,
                    workspace,
                    workspaceIndex: workspace.index(),
                    frame: {...frame, x: frame.x - monitor.x, y: frame.y - monitor.y},
                    maximized: window.get_maximize_flags(),
                    fullscreen: window.is_fullscreen(),
                    minimized: window.minimized,
                    sticky: window.is_on_all_workspaces(),
                    depth: ancestors.size - 1,
                });
                this._env.keepWorkspaceAlive(workspace, WORKSPACE_KEEP_ALIVE_MS);
            } catch (error) {
                this._env.logError(error);
            }
        }
        this._snapshot = {monitor: {...monitor}, windows: saved};
    }

    _delay(milliseconds) {
        return new Promise(resolve => {
            const id = this._env.setTimeout(() => {
                this._waits.delete(id);
                resolve(true);
            }, milliseconds);
            this._waits.set(id, resolve);
        });
    }

    restore(owner, connector) {
        this._checkOwner(owner);
        this._checkConnector(connector);
        if (!this.owner)
            throw new WindowRestoreError('NotRegistered', 'Register a Panelyra monitor first');
        if (this._restoring)
            return this._restoring;
        const promise = this._restore(connector).finally(() => {
            if (this._restoring === promise)
                this._restoring = null;
        });
        this._restoring = promise;
        return promise;
    }

    async _restore(connector) {
        const snapshot = this._snapshot;
        const generation = this._generation;
        const current = this._env.findMonitor(connector);
        if (!snapshot || this._env.isLocked() || !current)
            return 0;
        if (!compatibleMonitor(snapshot.monitor, current)) {
            // Host could not recreate the previous logical geometry. Do not
            // carry stale coordinates into an unrelated future lock cycle.
            this._snapshot = null;
            return 0;
        }
        const workspaces = new Map();
        const plans = [];
        const alive = new Set(this._env.listWindows());
        // Resolve workspaces once, in their original order. Dynamic workspaces
        // may have disappeared during a long lock; only recreate those needed.
        for (const saved of [...snapshot.windows].sort((a, b) => a.workspaceIndex - b.workspaceIndex)) {
            if (!alive.has(saved.window))
                continue;
            try {
                if (!workspaces.has(saved.workspace)) {
                    const workspace = this._env.resolveWorkspace(saved.workspace, saved.workspaceIndex);
                    if (!workspace)
                        continue;
                    this._env.keepWorkspaceAlive(workspace, WORKSPACE_KEEP_ALIVE_MS);
                    workspaces.set(saved.workspace, workspace);
                }
                plans.push({saved, workspace: workspaces.get(saved.workspace), applied: false});
            } catch (error) {
                this._env.logError(error);
            }
        }
        // Moving a parent can move attached dialogs too. Apply their own saved
        // frames after the parent, regardless of the compositor's window order.
        plans.sort((a, b) => a.saved.depth - b.saved.depth);
        // Configure requests on Wayland are asynchronous. Bounded retries let
        // clients resize after changing monitor/workspace without polling forever.
        for (const delay of RESTORE_DELAYS_MS) {
            if (!await this._delay(delay) || generation !== this._generation || this._env.isLocked())
                return 0;
            const monitor = this._env.findMonitor(connector);
            if (!monitor)
                return 0;
            if (!compatibleMonitor(snapshot.monitor, monitor)) {
                this._snapshot = null;
                return 0;
            }
            const managed = new Set(this._env.listWindows());
            for (const plan of plans) {
                if (!managed.has(plan.saved.window))
                    continue;
                try {
                    if (!this._matches(plan, monitor))
                        this._apply(plan, monitor);
                    plan.applied = true;
                } catch (error) {
                    this._env.logError(error);
                }
            }
        }
        // Give the last configure request a chance to settle before reporting.
        if (!await this._delay(150))
            return 0;
        if (generation !== this._generation || this._env.isLocked())
            return 0;
        const monitor = this._env.findMonitor(connector);
        if (!monitor)
            return 0;
        if (!compatibleMonitor(snapshot.monitor, monitor)) {
            this._snapshot = null;
            return 0;
        }
        const managed = new Set(this._env.listWindows());
        const restored = new Set();
        for (const plan of plans) {
            try {
                if (managed.has(plan.saved.window) && plan.applied && this._matches(plan, monitor))
                    restored.add(plan.saved.window);
            } catch (error) {
                this._env.logError(error);
            }
        }
        // Every completed attempt consumes this lock cycle, including clients
        // which refused geometry. The next lock must capture their latest layout
        // rather than silently reusing a stale snapshot. Only an interrupted
        // restore (re-lock/monitor loss) retains its snapshot for a safe retry.
        this._snapshot = null;
        this.connector = connector;
        return restored.size;
    }

    _targetFrame(saved, monitor) {
        return {...saved.frame, x: monitor.x + saved.frame.x, y: monitor.y + saved.frame.y};
    }

    _matches({saved, workspace}, monitor) {
        const window = saved.window;
        return window.get_monitor() === monitor.index &&
            (saved.sticky || window.get_workspace() === workspace) &&
            window.is_on_all_workspaces() === saved.sticky &&
            window.is_fullscreen() === saved.fullscreen &&
            window.get_maximize_flags() === saved.maximized &&
            window.minimized === saved.minimized &&
            (saved.fullscreen ||
                equalRect(window.get_frame_rect(), this._targetFrame(saved, monitor), saved.maximized));
    }

    _apply({saved, workspace}, monitor) {
        const window = saved.window;
        if (window.is_fullscreen() && !saved.fullscreen)
            window.unmake_fullscreen();
        const maximized = window.get_maximize_flags();
        const removeFlags = maximized & ~saved.maximized;
        if (removeFlags)
            window.set_unmaximize_flags(removeFlags);
        if (window.is_on_all_workspaces() && !saved.sticky)
            window.unstick();
        if (!saved.sticky && window.get_workspace() !== workspace)
            window.change_workspace(workspace);
        if (window.get_monitor() !== monitor.index)
            window.move_to_monitor(monitor.index);
        // Keeping an already-maximized/fullscreen window in that state preserves
        // Mutter's private "unmaximize" rectangle, which its public API cannot read.
        if (!saved.fullscreen && saved.maximized !== 3) {
            const frame = this._targetFrame(saved, monitor);
            const current = window.get_frame_rect();
            if (saved.maximized & 1) {
                frame.x = current.x;
                frame.width = current.width;
            }
            if (saved.maximized & 2) {
                frame.y = current.y;
                frame.height = current.height;
            }
            window.move_resize_frame(false, frame.x, frame.y, frame.width, frame.height);
        }
        const addFlags = saved.maximized & ~window.get_maximize_flags();
        if (addFlags)
            window.set_maximize_flags(addFlags);
        if (saved.fullscreen && !window.is_fullscreen())
            window.make_fullscreen();
        if (saved.sticky && !window.is_on_all_workspaces())
            window.stick();
        if (saved.minimized && !window.minimized)
            window.minimize();
        else if (!saved.minimized && window.minimized)
            window.unminimize();
    }
}
