// Run with: gjs -m tests/window-restore-tests.js
// Pure policy tests: no session bus connection, Shell changes, or real lock.
import {WindowRestore} from '../desktop/gnome-extension/panelyra-windows@tanosx.github.io/windowRestore.js';

let passed = 0;
function assert(condition, message = 'Assertion failed') {
    if (!condition)
        throw new Error(message);
}
function equal(actual, expected, message = 'Values differ') {
    assert(JSON.stringify(actual) === JSON.stringify(expected),
        `${message}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
}
function throws(operation, code) {
    try {
        operation();
    } catch (error) {
        equal(error.code, code);
        return;
    }
    throw new Error(`Expected ${code}`);
}
async function test(name, operation) {
    await operation();
    passed++;
    print(`ok ${passed} - ${name}`);
}

function workspace(index) {
    return {index: () => index};
}

class Window {
    constructor(space, options = {}) {
        Object.assign(this, {
            monitor: 1, space, frame: {x: 2040, y: 140, width: 900, height: 650},
            flags: 0, fullscreen: false, minimized: false, sticky: false,
            type: 'normal', parent: null, calls: [], resizeAttempts: 0,
        }, options);
    }
    get_monitor() { return this.monitor; }
    get_frame_rect() { return this.frame; }
    get_workspace() { return this.space; }
    get_maximize_flags() { return this.flags; }
    is_fullscreen() { return this.fullscreen; }
    is_on_all_workspaces() { return this.sticky; }
    get_transient_for() { return this.parent; }
    move_to_monitor(index) { this.calls.push('move'); this.monitor = index; }
    change_workspace(space) { this.calls.push('workspace'); this.space = space; }
    move_resize_frame(_userOp, x, y, width, height) {
        this.calls.push('resize');
        this.resizeAttempts++;
        if (this.failResize)
            throw new Error('Client rejected resize');
        this.frame = {x, y, width, height};
        if (this.clampFirst && this.resizeAttempts === 1)
            this.frame.x = 0;
    }
    set_maximize_flags(flags) { this.calls.push('maximize'); this.flags |= flags; }
    set_unmaximize_flags(flags) { this.calls.push('unmaximize'); this.flags &= ~flags; }
    make_fullscreen() { this.calls.push('fullscreen'); this.fullscreen = true; }
    unmake_fullscreen() { this.calls.push('unfullscreen'); this.fullscreen = false; }
    minimize() { this.calls.push('minimize'); this.minimized = true; }
    unminimize() { this.calls.push('unminimize'); this.minimized = false; }
    stick() { this.calls.push('stick'); this.sticky = true; }
    unstick() { this.calls.push('unstick'); this.sticky = false; }
}

function fixture() {
    const first = workspace(0);
    const second = workspace(1);
    const monitor = {index: 1, x: 1920, y: 0, width: 1920, height: 1200, scale: 1};
    const state = {
        locked: false, monitors: {'Meta-0': monitor}, windows: [], workspaces: [first, second],
        keeps: [], errors: [], delays: [], timed: 0, tick: null,
    };
    const environment = {
        isLocked: () => state.locked,
        findMonitor: connector => state.monitors[connector] ?? null,
        listWindows: () => [...state.windows],
        isEligible: window => ['normal', 'dialog', 'modal'].includes(window.type),
        keepWorkspaceAlive: (space, duration) => state.keeps.push([space, duration]),
        resolveWorkspace: (space, index) => {
            if (state.workspaces.includes(space))
                return space;
            while (state.workspaces.length <= index)
                state.workspaces.push(workspace(state.workspaces.length));
            return state.workspaces[index];
        },
        setTimeout: (callback, duration) => {
            state.delays.push(duration);
            return setTimeout(() => {
                state.timed++;
                state.tick?.(state.timed);
                callback();
            }, 0);
        },
        clearTimeout: id => clearTimeout(id),
        logError: error => state.errors.push(error.message),
    };
    const engine = new WindowRestore(environment);
    const add = options => {
        const window = new Window(second, options);
        state.windows.push(window);
        return window;
    };
    const capture = () => {
        assert(engine.register(':1.10', 'Meta-0'));
        engine.captureBeforeLock();
    };
    const reconnect = () => {
        delete state.monitors['Meta-0'];
        state.monitors['Meta-2'] = {...monitor, index: 2, x: -1920, y: 120};
        assert(engine.register(':1.10', 'Meta-2'));
    };
    return {state, engine, add, capture, reconnect, first, second};
}

await test('registration accepts only virtual connectors and an available unlocked monitor', () => {
    const {state, engine} = fixture();
    throws(() => engine.register(':1.10', 'HDMI-1'), 'InvalidConnector');
    throws(() => engine.register(':1.10', 'Meta-0\n'), 'InvalidConnector');
    throws(() => engine.register('org.example.Owner', 'Meta-0'), 'InvalidOwner');
    assert(!engine.register(':1.10', 'Meta-404'));
    state.locked = true;
    assert(!engine.register(':1.10', 'Meta-0'));
    equal(engine.owner, null);
});

await test('another sender cannot register, restore, or clear a snapshot', () => {
    const {engine, add, capture} = fixture();
    add();
    capture();
    throws(() => engine.register(':1.11', 'Meta-0'), 'NotOwner');
    throws(() => engine.restore(':1.11', 'Meta-0'), 'NotOwner');
    throws(() => engine.unregister(':1.11'), 'NotOwner');
    equal(engine.owner, ':1.10');
});

await test('restores frame offsets, workspace, minimized state, and new connector exactly once', async () => {
    const {state, engine, add, capture, reconnect, first, second} = fixture();
    const window = add({minimized: true});
    capture();
    window.monitor = 0;
    window.frame = {x: 10, y: 20, width: 500, height: 400};
    window.space = first;
    window.minimized = false;
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
    equal(window.frame, {x: -1800, y: 260, width: 900, height: 650});
    assert(window.space === second);
    assert(window.minimized);
    assert(state.keeps.every(([, duration]) => duration > 0 && duration <= 300000));
    window.frame.x = 123;
    equal(await engine.restore(':1.10', 'Meta-2'), 0);
    equal(window.frame.x, 123, 'Consumed snapshot must not move a window again');
});

await test('only captures app windows on the tablet plus associated nested dialogs', async () => {
    const {engine, add, capture, reconnect} = fixture();
    const parent = add();
    const other = add({monitor: 0});
    const dialog = add({monitor: 0, type: 'dialog', parent});
    const modal = add({monitor: 0, type: 'modal', parent: dialog});
    const shell = add({type: 'desktop'});
    const popup = add({type: 'popup', parent});
    capture();
    parent.monitor = 0;
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 3);
    for (const window of [parent, dialog, modal])
        equal(window.monitor, 2);
    for (const window of [other, shell, popup])
        equal(window.calls, []);
});

await test('repeated lock activation and reconnection cannot overwrite the pending snapshot', async () => {
    const {engine, add, capture, reconnect} = fixture();
    const window = add();
    capture();
    window.frame = {x: 1, y: 2, width: 300, height: 200};
    engine.captureBeforeLock();
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
    equal(window.frame, {x: -1800, y: 260, width: 900, height: 650});
});

await test('closed windows are ignored and never receive stale object calls', async () => {
    const {state, engine, add, capture, reconnect} = fixture();
    const closed = add();
    const alive = add();
    capture();
    state.windows = [alive];
    closed.get_monitor = () => { throw new Error('Destroyed object touched'); };
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
    equal(closed.calls, []);
    equal(state.errors, []);
});

await test('maximized, partial-maximized, fullscreen and sticky windows retain state without focus calls', async () => {
    const {engine, add, capture, reconnect} = fixture();
    const maximized = add({flags: 3});
    const partial = add({flags: 1});
    const fullscreen = add({fullscreen: true, flags: 3});
    const sticky = add({sticky: true});
    capture();
    for (const window of [maximized, partial, fullscreen, sticky])
        window.monitor = 0;
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 4);
    for (const window of [maximized, fullscreen]) {
        assert(!window.calls.includes('unmaximize'));
        assert(!window.calls.includes('resize'));
    }
    assert(!partial.calls.includes('unmaximize'));
    equal(partial.frame.y, 260);
    assert(sticky.sticky);
});

await test('state changed by monitor removal is restored including partial axes', async () => {
    const {engine, add, capture, reconnect} = fixture();
    const normal = add();
    const partial = add({flags: 2});
    const fullscreen = add({fullscreen: true});
    capture();
    normal.flags = 3;
    normal.fullscreen = true;
    normal.minimized = true;
    normal.sticky = true;
    partial.flags = 1;
    fullscreen.fullscreen = false;
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 3);
    equal([normal.flags, normal.fullscreen, normal.minimized, normal.sticky], [0, false, false, false]);
    equal(partial.flags, 2);
    assert(fullscreen.fullscreen);
});

await test('Wayland geometry clamping is repaired by a bounded later pass', async () => {
    const {state, engine, add, capture, reconnect} = fixture();
    const window = add({clampFirst: true});
    capture();
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
    equal(window.resizeAttempts, 2);
    assert(state.delays.length <= 5);
    assert(state.delays.reduce((sum, delay) => sum + delay, 0) <= 1500);
});

await test('a broken client does not block others and a finished cycle never reuses stale positions', async () => {
    const {state, engine, add, capture, reconnect} = fixture();
    const failed = add({failResize: true});
    const good = add();
    capture();
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
    assert(state.errors.length > 0);
    good.frame.x = 100;
    failed.failResize = false;
    equal(await engine.restore(':1.10', 'Meta-2'), 0);
    equal(good.frame.x, 100);
    failed.frame = {x: -1600, y: 400, width: 700, height: 500};
    engine.captureBeforeLock();
    failed.frame.x = 0;
    equal(await engine.restore(':1.10', 'Meta-2'), 2);
    equal(failed.frame, {x: -1600, y: 400, width: 700, height: 500});
});

await test('restore while locked keeps snapshot; incompatible resolution discards it without moving windows', async () => {
    const {state, engine, add, capture, reconnect} = fixture();
    const window = add();
    capture();
    reconnect();
    state.locked = true;
    equal(await engine.restore(':1.10', 'Meta-2'), 0);
    state.locked = false;
    state.monitors['Meta-2'].width = 800;
    equal(await engine.restore(':1.10', 'Meta-2'), 0);
    state.monitors['Meta-2'].width = 1920;
    equal(window.calls, []);
    equal(await engine.restore(':1.10', 'Meta-2'), 0);
    window.monitor = 2;
    engine.captureBeforeLock();
    window.monitor = 0;
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
});

await test('incompatible scale discards the old snapshot without moving windows', async () => {
    const {state, engine, add, capture, reconnect} = fixture();
    const window = add();
    capture();
    reconnect();
    state.monitors['Meta-2'].scale = 2;
    equal(await engine.restore(':1.10', 'Meta-2'), 0);
    state.monitors['Meta-2'].scale = 1;
    equal(await engine.restore(':1.10', 'Meta-2'), 0);
    equal(window.calls, []);
});

await test('partially maximized windows restore position and size on their free axis', async () => {
    const {engine, add, capture, reconnect} = fixture();
    const vertical = add({flags: 2});
    const horizontal = add({flags: 1});
    capture();
    vertical.frame = {x: 10, y: 0, width: 300, height: 1200};
    horizontal.frame = {x: 0, y: 10, width: 1920, height: 300};
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 2);
    equal([vertical.frame.x, vertical.frame.width], [-1800, 900]);
    equal([horizontal.frame.y, horizontal.frame.height], [260, 650]);
    equal([vertical.flags, horizontal.flags], [2, 1]);
    assert(!vertical.calls.includes('unmaximize'));
    assert(!horizontal.calls.includes('unmaximize'));
});

await test('parents restore before their transient dialogs even if Shell lists the dialog first', async () => {
    const {state, engine, add, capture, reconnect} = fixture();
    const parent = add();
    const dialog = add({type: 'dialog', parent});
    state.windows = [dialog, parent];
    const order = [];
    parent.move_to_monitor = index => { order.push('parent'); parent.monitor = index; };
    dialog.move_to_monitor = index => { order.push('dialog'); dialog.monitor = index; };
    capture();
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 2);
    equal(order, ['parent', 'dialog']);
});

await test('owner disappearance cancels queued work and erases all remembered windows', async () => {
    const {engine, add, capture, reconnect} = fixture();
    const window = add();
    capture();
    reconnect();
    const pending = engine.restore(':1.10', 'Meta-2');
    engine.clear();
    equal(await pending, 0);
    equal(window.calls, []);
    assert(engine.register(':1.11', 'Meta-2'));
    equal(await engine.restore(':1.11', 'Meta-2'), 0);
});

await test('locking again during retries aborts work and preserves the earlier snapshot', async () => {
    const {state, engine, add, capture, reconnect} = fixture();
    const window = add({clampFirst: true});
    capture();
    reconnect();
    state.tick = tick => { if (tick === 2) state.locked = true; };
    equal(await engine.restore(':1.10', 'Meta-2'), 0);
    equal(window.resizeAttempts, 1);
    state.locked = false;
    state.tick = null;
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
});

await test('a removed dynamic workspace is recreated without activating it', async () => {
    const {state, engine, add, capture, reconnect, first} = fixture();
    const window = add();
    capture();
    state.workspaces = [first];
    window.space = first;
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
    equal(state.workspaces.length, 2);
    assert(window.space === state.workspaces[1]);
});

await test('capture failure in one window does not discard the others', async () => {
    const {state, engine, add, capture, reconnect} = fixture();
    const broken = add();
    add();
    broken.get_frame_rect = () => { throw new Error('Window closed while snapshotting'); };
    capture();
    reconnect();
    equal(await engine.restore(':1.10', 'Meta-2'), 1);
    equal(state.errors.length, 1);
});

await test('overlapping Restore calls share one bounded restore and Unregister discards it', async () => {
    const {engine, add, capture, reconnect} = fixture();
    const window = add();
    capture();
    reconnect();
    const first = engine.restore(':1.10', 'Meta-2');
    const second = engine.restore(':1.10', 'Meta-2');
    assert(first === second);
    engine.unregister(':1.10');
    equal(await first, 0);
    equal(await second, 0);
    equal(window.calls, []);
});

print(`1..${passed}`);
