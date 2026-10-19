"""Input for the LED Tetris game.

Reads Linux evdev devices directly (USB gamepads, USB/Bluetooth keyboards) so
nothing has to be installed with pip - handy on a Pi Zero. A raw-terminal
fallback is included for playing over SSH while testing.

Needs read access to /dev/input/event*: either run as root or add the user to
the `input` group (`sudo usermod -aG input $USER`, then log out and back in).
"""

import fcntl
import glob
import json
import os
import select
import struct
import sys
import termios
import tty

import tetris as T

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

_EVENT_FMT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)

_EVIOCGNAME = 0x80000000 | (256 << 16) | (0x45 << 8) | 0x06
_EVIOCGRAB = 0x40000000 | (4 << 16) | (0x45 << 8) | 0x90


def _EVIOCGABS(axis):
    return 0x80000000 | (24 << 16) | (0x45 << 8) | (0x40 + axis)


ABS_X, ABS_Y = 0x00, 0x01
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11

# code -> action, for both keyboard keys and gamepad buttons
DEFAULT_KEYS = {
    105: T.LEFT,        # KEY_LEFT
    106: T.RIGHT,       # KEY_RIGHT
    108: T.SOFT_DROP,   # KEY_DOWN
    103: T.HARD_DROP,   # KEY_UP
    57: T.HARD_DROP,    # KEY_SPACE
    45: T.ROT_CW,       # KEY_X
    44: T.ROT_CCW,      # KEY_Z
    30: T.ROT_180,      # KEY_A
    46: T.HOLD,         # KEY_C
    42: T.HOLD,         # KEY_LEFTSHIFT
    25: T.PAUSE,        # KEY_P
    1: T.PAUSE,         # KEY_ESC
    19: T.RESTART,      # KEY_R
    16: T.QUIT,         # KEY_Q
    304: T.ROT_CW,      # BTN_SOUTH / A
    305: T.ROT_CCW,     # BTN_EAST / B
    307: T.ROT_180,     # BTN_NORTH / X
    308: T.HOLD,        # BTN_WEST / Y
    310: T.HOLD,        # BTN_TL
    311: T.HARD_DROP,   # BTN_TR
    314: T.RESTART,     # BTN_SELECT
    315: T.PAUSE,       # BTN_START
    288: T.ROT_CW,      # BTN_TRIGGER
    289: T.ROT_CCW,     # BTN_THUMB
    290: T.ROT_180,     # BTN_THUMB2
    291: T.HOLD,        # BTN_TOP
}

# axis -> (action when negative, action when positive)
DEFAULT_AXES = {
    ABS_HAT0X: (T.LEFT, T.RIGHT),
    ABS_HAT0Y: (T.HARD_DROP, T.SOFT_DROP),
    ABS_X: (T.LEFT, T.RIGHT),
    ABS_Y: (T.HARD_DROP, T.SOFT_DROP),
}

# terminal escape sequences / characters -> action
TERMINAL_KEYS = {
    "\x1b[D": T.LEFT,
    "\x1b[C": T.RIGHT,
    "\x1b[B": T.SOFT_DROP,
    "\x1b[A": T.HARD_DROP,
    " ": T.HARD_DROP,
    "x": T.ROT_CW,
    "z": T.ROT_CCW,
    "a": T.ROT_180,
    "c": T.HOLD,
    "p": T.PAUSE,
    "r": T.RESTART,
    "q": T.QUIT,
    "\x03": T.QUIT,
}

CONFIG_PATH = os.path.expanduser("~/.config/wled-tetris/controls.json")


def device_name(fd):
    try:
        buf = bytearray(256)
        fcntl.ioctl(fd, _EVIOCGNAME, buf)
        return buf.split(b"\x00", 1)[0].decode("utf-8", "replace")
    except OSError:
        return "?"


def _absinfo(fd, axis):
    try:
        buf = bytearray(24)
        fcntl.ioctl(fd, _EVIOCGABS(axis), buf)
        _value, minimum, maximum, _fuzz, flat, _res = struct.unpack("6i", buf)
        return minimum, maximum, flat
    except OSError:
        return None


def list_devices():
    """(path, name) for every readable /dev/input/event* device."""
    out = []
    for path in sorted(glob.glob("/dev/input/event*"),
                       key=lambda p: int(p.rsplit("event", 1)[1])):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            out.append((path, device_name(fd)))
        finally:
            os.close(fd)
    return out


class EvdevDevice:
    """One /dev/input/eventN, decoded into (action, pressed) pairs."""

    def __init__(self, path, keys, axes, grab=False):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.name = device_name(self.fd)
        self._keys = keys
        self._axes = axes
        self._axis_state = {}
        self._thresholds = {}
        self.grabbed = False
        if grab:
            try:
                fcntl.ioctl(self.fd, _EVIOCGRAB, 1)
                self.grabbed = True
            except OSError:
                pass
        for axis in self._axes:
            info = _absinfo(self.fd, axis)
            if info:
                minimum, maximum, flat = info
                center = (minimum + maximum) / 2.0
                span = max(1.0, (maximum - minimum) / 2.0)
                dead = max(flat, span * 0.45) if span > 2 else 0.5
                self._thresholds[axis] = (center, dead)

    def close(self):
        if self.grabbed:
            try:
                fcntl.ioctl(self.fd, _EVIOCGRAB, 0)
            except OSError:
                pass
        os.close(self.fd)

    def read(self):
        events = []
        while True:
            try:
                data = os.read(self.fd, _EVENT_SIZE * 64)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if not data:
                break
            for i in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
                _s, _us, etype, code, value = struct.unpack_from(_EVENT_FMT, data, i)
                if etype == EV_KEY:
                    action = self._keys.get(code)
                    if action and value != 2:  # 2 is the kernel's auto-repeat
                        events.append((action, bool(value)))
                elif etype == EV_ABS and code in self._axes:
                    events.extend(self._axis_events(code, value))
            if len(data) < _EVENT_SIZE * 64:
                break
        return events

    def _axis_events(self, axis, value):
        center, dead = self._thresholds.get(axis, (0.0, 0.5))
        if value < center - dead:
            direction = -1
        elif value > center + dead:
            direction = 1
        else:
            direction = 0
        previous = self._axis_state.get(axis, 0)
        if direction == previous:
            return []
        self._axis_state[axis] = direction
        neg, pos = self._axes[axis]
        out = []
        if previous:
            out.append((neg if previous < 0 else pos, False))
        if direction:
            out.append((neg if direction < 0 else pos, True))
        return out


class TerminalInput:
    """Raw-mode stdin. Only produces taps - the terminal gives no key releases."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        os.set_blocking(self.fd, False)
        self._pending = ""

    def close(self):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)

    def read(self):
        try:
            chunk = os.read(self.fd, 64).decode("utf-8", "ignore")
        except (BlockingIOError, InterruptedError, OSError):
            chunk = ""
        if not chunk:
            return []
        self._pending += chunk
        events = []
        while self._pending:
            if self._pending.startswith("\x1b["):
                if len(self._pending) < 3:
                    break
                seq, self._pending = self._pending[:3], self._pending[3:]
            else:
                seq, self._pending = self._pending[:1], self._pending[1:]
            action = TERMINAL_KEYS.get(seq.lower() if len(seq) == 1 else seq)
            if action:
                events.append((action, True))
                events.append((action, False))
        return events


class InputHub:
    def __init__(self, device_paths=None, use_terminal=False, mapping=None, grab=False):
        keys, axes = load_mapping(mapping)
        self.devices = []
        self.terminal = None
        paths = device_paths if device_paths else [p for p, _ in list_devices()]
        for path in paths:
            try:
                self.devices.append(EvdevDevice(path, keys, axes, grab=grab))
            except OSError as exc:
                if device_paths:
                    raise SystemExit("cannot open %s: %s" % (path, exc))
        if use_terminal:
            self.terminal = TerminalInput()

    @property
    def sources(self):
        return self.devices + ([self.terminal] if self.terminal else [])

    def poll(self):
        fds = [d.fd for d in self.sources]
        if not fds:
            return []
        ready, _, _ = select.select(fds, [], [], 0)
        if not ready:
            return []
        events = []
        for source in self.sources:
            if source.fd in ready:
                events.extend(source.read())
        return events

    def close(self):
        for source in self.sources:
            try:
                source.close()
            except OSError:
                pass

    def describe(self):
        parts = ["%s (%s)%s" % (d.path, d.name, " grabbed" if d.grabbed else "")
                 for d in self.devices]
        if self.terminal:
            parts.append("terminal")
        return ", ".join(parts) if parts else "none"


class Repeater:
    """Turns held-down left/right into repeats (DAS/ARR), as Tetris expects.

    Soft drop is not repeated here: the engine speeds gravity up while the key
    is held, which keeps the drop rate tied to the game clock.
    """

    def __init__(self, das=0.17, arr=0.04):
        self.das = das
        self.arr = arr
        self.held = set()
        self._shift = None
        self._next_repeat = 0.0

    def feed(self, events, now):
        """Returns the discrete actions to apply this frame."""
        actions = []
        for action, pressed in events:
            if pressed:
                self.held.add(action)
            else:
                self.held.discard(action)
            if action in (T.LEFT, T.RIGHT):
                if pressed:
                    self._shift = action
                    self._next_repeat = now + self.das
                    actions.append(action)
                elif self._shift == action:
                    # fall back to the other direction if it is still held
                    other = T.RIGHT if action == T.LEFT else T.LEFT
                    self._shift = other if other in self.held else None
                    self._next_repeat = now + self.das
            elif pressed:
                actions.append(action)
        if self._shift and now >= self._next_repeat:
            actions.append(self._shift)
            self._next_repeat = now + self.arr
        return actions

    @property
    def soft_dropping(self):
        return T.SOFT_DROP in self.held


def load_mapping(path=None):
    """Load a controls.json, falling back to the built-in defaults."""
    keys = dict(DEFAULT_KEYS)
    axes = dict(DEFAULT_AXES)
    path = path or (CONFIG_PATH if os.path.exists(CONFIG_PATH) else None)
    if not path:
        return keys, axes
    with open(path) as fh:
        cfg = json.load(fh)
    if cfg.get("replace"):
        keys, axes = {}, {}
    for code, action in (cfg.get("keys") or {}).items():
        keys[int(code)] = action
    for axis, pair in (cfg.get("axes") or {}).items():
        axes[int(axis)] = (pair[0], pair[1])
    return keys, axes


MAPPABLE = [
    (T.LEFT, "move left"),
    (T.RIGHT, "move right"),
    (T.SOFT_DROP, "soft drop"),
    (T.HARD_DROP, "hard drop"),
    (T.ROT_CW, "rotate clockwise"),
    (T.ROT_CCW, "rotate counter-clockwise"),
    (T.ROT_180, "rotate 180"),
    (T.HOLD, "hold piece"),
    (T.PAUSE, "pause"),
    (T.RESTART, "restart"),
    (T.QUIT, "quit"),
]


def map_controller(device_paths=None, out_path=CONFIG_PATH):
    """Interactively record a button/axis for each action and save it."""
    paths = device_paths if device_paths else [p for p, _ in list_devices()]
    devices = []
    for path in paths:
        try:
            devices.append(EvdevDevice(path, {}, {}))
        except OSError:
            continue
    if not devices:
        raise SystemExit("no readable /dev/input/event* devices "
                         "(try sudo, or add yourself to the `input` group)")
    print("Found: %s" % ", ".join("%s (%s)" % (d.path, d.name) for d in devices))
    print("Press the control for each action. Press ENTER here to skip one.\n")
    keys, axes = {}, {}
    os.set_blocking(sys.stdin.fileno(), False)
    try:
        for action, description in MAPPABLE:
            print("  %-22s -> " % description, end="", flush=True)
            got = _await_control(devices, action, keys, axes)
            print(got)
    finally:
        for device in devices:
            device.close()
        os.set_blocking(sys.stdin.fileno(), True)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump({"replace": True,
                   "keys": {str(k): v for k, v in keys.items()},
                   "axes": {str(k): list(v) for k, v in axes.items()}}, fh, indent=2)
    print("\nSaved %s" % out_path)
    return out_path


def _await_control(devices, action, keys, axes):
    """Block until a button or axis moves; record it against `action`."""
    fds = [d.fd for d in devices] + [sys.stdin.fileno()]
    centers = {}
    while True:
        ready, _, _ = select.select(fds, [], [], None)
        if sys.stdin.fileno() in ready:
            try:
                os.read(sys.stdin.fileno(), 64)
            except OSError:
                pass
            return "skipped"
        for device in devices:
            if device.fd not in ready:
                continue
            try:
                data = os.read(device.fd, _EVENT_SIZE * 64)
            except OSError:
                continue
            for i in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
                _s, _us, etype, code, value = struct.unpack_from(_EVENT_FMT, data, i)
                if etype == EV_KEY and value == 1:
                    keys[code] = action
                    return "key/button code %d" % code
                if etype == EV_ABS:
                    base = centers.setdefault(code, value)
                    info = _absinfo(device.fd, code)
                    span = max(1.0, (info[1] - info[0]) / 2.0) if info else 1.0
                    if abs(value - base) > max(1.0, span * 0.5):
                        negative, positive = axes.get(code, (None, None))
                        if value < base:
                            negative = action
                        else:
                            positive = action
                        axes[code] = (negative or action, positive or action)
                        return "axis %d %s" % (code, "-" if value < base else "+")
