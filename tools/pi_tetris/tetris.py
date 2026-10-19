"""Tetris engine: SRS rotation with wall kicks, 7-bag randomiser, hold,
lock delay, ghost piece and guideline scoring.

The engine is display- and input-agnostic: feed it discrete actions and call
update() with a monotonic timestamp.
"""

import random

# --- actions ---------------------------------------------------------------
LEFT = "left"
RIGHT = "right"
SOFT_DROP = "soft_drop"
HARD_DROP = "hard_drop"
ROT_CW = "rot_cw"
ROT_CCW = "rot_ccw"
ROT_180 = "rot_180"
HOLD = "hold"
PAUSE = "pause"
RESTART = "restart"
QUIT = "quit"

# --- states ----------------------------------------------------------------
PLAYING = "playing"
CLEARING = "clearing"
GAME_OVER = "game_over"
PAUSED = "paused"

# --- pieces ----------------------------------------------------------------
# id, box size, spawn cells, colour. Cell coordinates are (x, y) inside the
# rotation box with y growing downwards.
PIECES = {
    "I": (1, 4, ((0, 1), (1, 1), (2, 1), (3, 1)), (0, 255, 255)),
    "J": (2, 3, ((0, 0), (0, 1), (1, 1), (2, 1)), (0, 64, 255)),
    "L": (3, 3, ((2, 0), (0, 1), (1, 1), (2, 1)), (255, 128, 0)),
    "O": (4, 2, ((0, 0), (1, 0), (0, 1), (1, 1)), (255, 224, 0)),
    "S": (5, 3, ((1, 0), (2, 0), (0, 1), (1, 1)), (0, 255, 64)),
    "T": (6, 3, ((1, 0), (0, 1), (1, 1), (2, 1)), (176, 0, 255)),
    "Z": (7, 3, ((0, 0), (1, 0), (1, 1), (2, 1)), (255, 0, 48)),
}
ORDER = "IJLOSZT"
GARBAGE_COLOR = (96, 96, 96)


def _rotations(box, cells):
    """All four rotation states, derived by rotating inside the box."""
    states = [tuple(sorted(cells))]
    cur = cells
    for _ in range(3):
        cur = tuple((box - 1 - y, x) for x, y in cur)
        states.append(tuple(sorted(cur)))
    return states


SHAPES = {name: _rotations(box, cells) for name, (_, box, cells, _) in PIECES.items()}
BOXES = {name: box for name, (_, box, _, _) in PIECES.items()}

# SRS wall kicks. Source tables are written with y pointing up; the y values
# here are negated so they match the screen coordinates used everywhere else.
_KICKS_JLSTZ = {
    (0, 1): ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
    (1, 0): ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
    (1, 2): ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
    (2, 1): ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
    (2, 3): ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
    (3, 2): ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
    (3, 0): ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
    (0, 3): ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
}
_KICKS_I = {
    (0, 1): ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),
    (1, 0): ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),
    (1, 2): ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),
    (2, 1): ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),
    (2, 3): ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),
    (3, 2): ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),
    (3, 0): ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),
    (0, 3): ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),
}

LINE_SCORES = {1: 100, 2: 300, 3: 500, 4: 800}

LOCK_DELAY = 0.5
MAX_LOCK_RESETS = 15
CLEAR_FLASH = 0.16
GAME_OVER_WIPE = 1.1


def gravity_for(level):
    """Seconds per row, per the Tetris guideline curve."""
    lvl = max(1, min(20, level))
    return max(0.004, (0.8 - (lvl - 1) * 0.007) ** (lvl - 1))


class Piece:
    __slots__ = ("name", "rot", "x", "y")

    def __init__(self, name, x, y, rot=0):
        self.name = name
        self.rot = rot
        self.x = x
        self.y = y

    def cells(self, rot=None, x=None, y=None):
        rot = self.rot if rot is None else rot
        x = self.x if x is None else x
        y = self.y if y is None else y
        return [(x + cx, y + cy) for cx, cy in SHAPES[self.name][rot]]

    @property
    def pid(self):
        return PIECES[self.name][0]

    @property
    def color(self):
        return PIECES[self.name][3]


class Tetris:
    def __init__(self, cols=10, rows=20, start_level=1, seed=None, preview=3):
        self.cols = cols
        self.rows = rows
        self.start_level = max(1, start_level)
        self.preview = preview
        self._rng = random.Random(seed)
        self.reset()

    # --- lifecycle -------------------------------------------------------

    def reset(self, now=0.0):
        self.board = [[0] * self.cols for _ in range(self.rows)]
        self.bag = []
        self.queue = [self._next_name() for _ in range(max(self.preview, 1))]
        self.hold = None
        self.hold_used = False
        self.score = 0
        self.lines = 0
        self.level = self.start_level
        self.combo = -1
        self.state = PLAYING
        self.piece = None
        self.clear_rows = []
        self.phase_start = now
        self.last_gravity = now
        self.lock_start = None
        self.lock_resets = 0
        self.last_clear = 0
        self._spawn(now)

    def _next_name(self):
        if not self.bag:
            self.bag = list(ORDER)
            self._rng.shuffle(self.bag)
        return self.bag.pop()

    def _spawn(self, now, name=None):
        name = name or self.queue.pop(0)
        while len(self.queue) < max(self.preview, 1):
            self.queue.append(self._next_name())
        box = BOXES[name]
        x = (self.cols - box) // 2
        y = -min(cy for _, cy in SHAPES[name][0])
        piece = Piece(name, x, y)
        if self._collides(piece.cells()):
            self.piece = piece
            self.state = GAME_OVER
            self.phase_start = now
            return
        self.piece = piece
        self.last_gravity = now
        self.lock_start = None
        self.lock_resets = 0
        self.hold_used = False

    # --- collision -------------------------------------------------------

    def _collides(self, cells):
        for x, y in cells:
            if x < 0 or x >= self.cols or y >= self.rows:
                return True
            if y >= 0 and self.board[y][x]:
                return True
        return False

    # --- player actions --------------------------------------------------

    def action(self, act, now):
        if act == PAUSE:
            if self.state == PLAYING:
                self.state = PAUSED
            elif self.state == PAUSED:
                self.state = PLAYING
                self.last_gravity = now
                if self.lock_start is not None:
                    self.lock_start = now
            return
        if act == RESTART:
            self.reset(now)
            return
        if self.state == GAME_OVER:
            if act in (HARD_DROP, ROT_CW, ROT_CCW) and now - self.phase_start > GAME_OVER_WIPE:
                self.reset(now)
            return
        if self.state != PLAYING:
            return

        if act == LEFT:
            self._shift(-1, now)
        elif act == RIGHT:
            self._shift(1, now)
        elif act == SOFT_DROP:
            if self._try_move(0, 1):
                self.score += 1
                self.last_gravity = now
                self._reset_lock_timer(now, moved=False)
            else:
                self._begin_lock(now)
        elif act == HARD_DROP:
            dropped = 0
            while self._try_move(0, 1):
                dropped += 1
            self.score += 2 * dropped
            self._lock(now)
        elif act == ROT_CW:
            self._rotate(1, now)
        elif act == ROT_CCW:
            self._rotate(-1, now)
        elif act == ROT_180:
            self._rotate(1, now, _half=True)
        elif act == HOLD:
            self._do_hold(now)

    def _shift(self, dx, now):
        if self._try_move(dx, 0):
            self._reset_lock_timer(now)

    def _try_move(self, dx, dy):
        p = self.piece
        if p is None:
            return False
        if self._collides(p.cells(x=p.x + dx, y=p.y + dy)):
            return False
        p.x += dx
        p.y += dy
        return True

    def _rotate(self, direction, now, _half=False):
        p = self.piece
        if p is None:
            return False
        if _half:
            # 180 is not part of SRS: try two quarter turns, and if the second
            # one is blocked undo the first so the piece never ends up at 90.
            saved = (p.rot, p.x, p.y)
            if self._rotate(1, now) and self._rotate(1, now):
                return True
            p.rot, p.x, p.y = saved
            target = (p.rot + 2) % 4
            if not self._collides(p.cells(rot=target)):
                p.rot = target
                self._reset_lock_timer(now)
                return True
            return False
        target = (p.rot + direction) % 4
        kicks = (_KICKS_I if p.name == "I" else _KICKS_JLSTZ).get((p.rot, target), ((0, 0),))
        for dx, dy in kicks:
            if not self._collides(p.cells(rot=target, x=p.x + dx, y=p.y + dy)):
                p.rot = target
                p.x += dx
                p.y += dy
                self._reset_lock_timer(now)
                return True
        return False

    def _do_hold(self, now):
        if self.hold_used or self.piece is None:
            return
        swap, self.hold = self.hold, self.piece.name
        self._spawn(now, swap)
        self.hold_used = True

    # --- timing ----------------------------------------------------------

    def _reset_lock_timer(self, now, moved=True):
        if self.lock_start is not None and self.lock_resets < MAX_LOCK_RESETS:
            self.lock_start = now
            if moved:
                self.lock_resets += 1

    def _begin_lock(self, now):
        if self.lock_start is None:
            self.lock_start = now

    def _grounded(self):
        p = self.piece
        return p is not None and self._collides(p.cells(y=p.y + 1))

    def update(self, now, soft_dropping=False):
        if self.state == CLEARING:
            if now - self.phase_start >= CLEAR_FLASH:
                self._collapse(now)
            return
        if self.state != PLAYING:
            return

        interval = gravity_for(self.level)
        if soft_dropping:
            interval = min(interval, max(interval / 20.0, 1.0 / 60.0))
        if now - self.last_gravity > 1.0:
            self.last_gravity = now  # came back from a stall, do not catch up
        while now - self.last_gravity >= interval:
            self.last_gravity += interval
            if self._try_move(0, 1):
                self.lock_start = None
                self.lock_resets = 0  # reached a new lowest row
                if soft_dropping:
                    self.score += 1
            else:
                self._begin_lock(now)
                break

        if self._grounded():
            self._begin_lock(now)
            if now - self.lock_start >= LOCK_DELAY:
                self._lock(now)
        else:
            self.lock_start = None

    # --- locking and clearing -------------------------------------------

    def _lock(self, now):
        p = self.piece
        if p is None:
            return
        for x, y in p.cells():
            if 0 <= y < self.rows and 0 <= x < self.cols:
                self.board[y][x] = p.pid
        self.piece = None
        full = [y for y in range(self.rows) if all(self.board[y])]
        if full:
            self.clear_rows = full
            self.state = CLEARING
            self.phase_start = now
        else:
            self.combo = -1
            self._spawn(now)

    def _collapse(self, now):
        count = len(self.clear_rows)
        for y in sorted(self.clear_rows):
            del self.board[y]
            self.board.insert(0, [0] * self.cols)
        self.lines += count
        self.combo += 1
        self.last_clear = count
        self.score += LINE_SCORES.get(count, 0) * self.level
        if self.combo > 0:
            self.score += 50 * self.combo * self.level
        self.level = self.start_level + self.lines // 10
        self.clear_rows = []
        self.state = PLAYING
        self._spawn(now)

    # --- queries for the renderer ---------------------------------------

    def ghost_y(self):
        p = self.piece
        if p is None:
            return None
        dy = 0
        while not self._collides(p.cells(y=p.y + dy + 1)):
            dy += 1
        return p.y + dy

    def wipe_rows(self, now):
        """How many rows of the game-over wipe are filled in, bottom up."""
        t = min(1.0, (now - self.phase_start) / GAME_OVER_WIPE)
        return int(t * self.rows)
