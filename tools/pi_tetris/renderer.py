"""Draws a Tetris game onto a Canvas, laying itself out to fit the display.

Two templates are chosen by how much room is left beside the playfield:

  wide HUD   (e.g. a 48x32 panel) - playfield centred on the left, HUD packed
             into the column beside it
  narrow HUD (e.g. the same panel mounted portrait, 32x48) - playfield at the
             top, HUD split between the side column and a strip underneath

Both feed the same zone packer, so widgets that do not fit are simply dropped
instead of spilling over the edge.
"""

import font
import tetris as T
from canvas import dim, rgb

BG = rgb(0, 0, 0)
FRAME = rgb(18, 18, 26)
GRID = rgb(6, 6, 9)
LABEL = rgb(70, 70, 85)
VALUE = rgb(190, 190, 200)
LINES_COLOR = rgb(60, 170, 190)
LEVEL_COLOR = rgb(200, 140, 40)
FLASH = rgb(255, 255, 255)
WIPE = rgb(70, 70, 70)
GHOST_FACTOR = 0.16
PREVIEW_SCALE = 2
PREVIEW_CELLS = (4, 2)  # every piece fits in a 4x2 cell box in its spawn state
MIN_HUD = 8
WIDE_HUD = 20


class Packer:
    """Flows fixed-size widgets through a list of (x, y, w, h) zones.

    Inside a zone widgets stack downwards, then start a new column. Each zone
    keeps its own cursor and an item is placed in the first zone it fits, so a
    widget that is too wide for one zone does not block the ones after it.
    """

    def __init__(self, zones, gap=1):
        self.zones = [tuple(z) for z in zones]
        self.gap = gap
        self._cursors = [[z[0], z[1], 0] for z in self.zones]  # x, y, column width

    def place(self, w, h):
        for i, (zx, zy, zw, zh) in enumerate(self.zones):
            cx, cy, col_w = self._cursors[i]
            if cy + h <= zy + zh and cx + w <= zx + zw:
                self._cursors[i] = [cx, cy + h + self.gap, max(col_w, w)]
                return cx, cy
            nx = cx + col_w + self.gap
            if col_w and nx + w <= zx + zw and h <= zh:
                self._cursors[i] = [nx, zy + h + self.gap, w]
                return nx, zy
        return None

    def place_first(self, variants):
        """Place the first variant that fits. Variants are (w, h, draw)."""
        for w, h, draw in variants:
            spot = self.place(w, h)
            if spot:
                draw(spot[0], spot[1])
                return True
        return False


class Layout:
    def __init__(self, canvas_w, canvas_h, cols, rows):
        self.canvas_w = canvas_w
        self.canvas_h = canvas_h
        self.cols = cols
        self.rows = rows
        self.scale = self._best_scale(MIN_HUD) or self._best_scale(0) or 1
        self.field_w = cols * self.scale
        self.field_h = rows * self.scale
        hud_x = 2 + self.field_w + 1
        hud_w = max(0, canvas_w - hud_x)
        self.wide = hud_w >= WIDE_HUD

        if self.wide:
            self.field_x = 1
            self.field_y = max(1, (canvas_h - self.field_h) // 2)
            self.zones = [(hud_x, 0, hud_w, canvas_h)]
        else:
            self.field_x = 1
            self.field_y = 1
            zones = []
            if hud_w >= MIN_HUD:
                zones.append((hud_x, 0, hud_w, self.field_h + 2))
            strip_y = self.field_y + self.field_h + 2
            if canvas_h - strip_y >= font.GLYPH_H:
                zones.append((0, strip_y, canvas_w, canvas_h - strip_y))
            self.zones = zones
        self.preview_scale = max(1, min(PREVIEW_SCALE, self._widest_zone() // PREVIEW_CELLS[0]))
        self.fits = (self.rows * self.scale + 2 <= canvas_h
                     and self.cols * self.scale + 2 <= canvas_w)

    def _widest_zone(self):
        return max((z[2] for z in self.zones), default=0)

    def _best_scale(self, extra):
        best = 0
        for s in range(1, 17):
            if self.rows * s + 2 <= self.canvas_h and self.cols * s + 2 + extra <= self.canvas_w:
                best = s
        return best

    def cell_rect(self, cx, cy):
        s = self.scale
        return self.field_x + cx * s, self.field_y + cy * s, s, s


class Renderer:
    def __init__(self, canvas, cols, rows, show_ghost=True, show_grid=False):
        self.canvas = canvas
        self.layout = Layout(canvas.width, canvas.height, cols, rows)
        self.show_ghost = show_ghost
        self.show_grid = show_grid
        self._colors = {pid: rgb(*color) for pid, _, _, color in T.PIECES.values()}
        self._ghosts = {pid: dim(c, GHOST_FACTOR) for pid, c in self._colors.items()}

    # --- helpers ---------------------------------------------------------

    def _block(self, x, y, color):
        s = self.layout.scale
        self.canvas.fill_rect(x, y, s, s, color)
        if s >= 3:
            # a darker inner notch gives big cells a bit of definition
            self.canvas.set(x + s - 1, y + s - 1, dim(color, 0.45))

    def _cell(self, cx, cy, color):
        x, y, _, _ = self.layout.cell_rect(cx, cy)
        self._block(x, y, color)

    def _piece_preview(self, x, y, name, scale, factor=1.0):
        if name is None:
            return
        cells = T.SHAPES[name][0]
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        w = max(xs) - min(xs) + 1
        h = max(ys) - min(ys) + 1
        ox = x + (PREVIEW_CELLS[0] - w) * scale // 2 - min(xs) * scale
        oy = y + (PREVIEW_CELLS[1] - h) * scale // 2 - min(ys) * scale
        color = rgb(*T.PIECES[name][3])
        if factor != 1.0:
            color = dim(color, factor)
        for cx, cy in cells:
            self.canvas.fill_rect(ox + cx * scale, oy + cy * scale, scale, scale, color)

    def _text(self, x, y, text, color, scale=1):
        font.draw_text(self.canvas, x, y, text, color, scale)

    def _centered(self, candidates, y, color):
        """Draw the first candidate string that fits the canvas width."""
        for text in candidates:
            w = font.text_width(text)
            if w <= self.canvas.width:
                self._text((self.canvas.width - w) // 2, y, text, color)
                return True
        return False

    # --- main ------------------------------------------------------------

    def render(self, game, now):
        lay = self.layout
        c = self.canvas
        c.clear()
        c.rect(lay.field_x - 1, lay.field_y - 1, lay.field_w + 2, lay.field_h + 2, FRAME)

        if self.show_grid and lay.scale >= 3:
            for cy in range(game.rows):
                for cx in range(game.cols):
                    x, y, _, _ = lay.cell_rect(cx, cy)
                    c.set(x, y, GRID)

        wipe_from = game.rows - game.wipe_rows(now) if game.state == T.GAME_OVER else game.rows
        flashing = set(game.clear_rows) if game.state == T.CLEARING else set()

        for cy in range(game.rows):
            if cy >= wipe_from:
                for cx in range(game.cols):
                    self._cell(cx, cy, WIPE)
                continue
            if cy in flashing:
                for cx in range(game.cols):
                    self._cell(cx, cy, FLASH)
                continue
            row = game.board[cy]
            for cx in range(game.cols):
                pid = row[cx]
                if pid:
                    self._cell(cx, cy, self._colors[pid])

        if game.piece is not None and game.state in (T.PLAYING, T.PAUSED):
            p = game.piece
            if self.show_ghost:
                gy = game.ghost_y()
                if gy is not None and gy != p.y:
                    for x, y in p.cells(y=gy):
                        if 0 <= y < game.rows:
                            self._cell(x, y, self._ghosts[p.pid])
            color = self._colors[p.pid]
            for x, y in p.cells():
                if 0 <= y < game.rows:
                    self._cell(x, y, color)

        self._render_hud(game)

        if game.state == T.PAUSED:
            self._centered(("PAUSED", "PAUSE", "II"), (c.height - font.GLYPH_H) // 2, VALUE)
        elif game.state == T.GAME_OVER and game.wipe_rows(now) >= game.rows:
            y = (c.height - font.GLYPH_H * 2 - 2) // 2
            if self._centered(("GAME OVER", "OVER"), y, FLASH):
                self._centered((str(game.score),), y + font.GLYPH_H + 2, VALUE)

    def _render_hud(self, game):
        """Lay the HUD out as groups, each with fallbacks for tight zones."""
        lay = self.layout
        if not lay.zones:
            return
        packer = Packer(lay.zones)
        ps = lay.preview_scale
        pw, ph = PREVIEW_CELLS[0] * ps, PREVIEW_CELLS[1] * ps
        lh = font.GLYPH_H

        def stack(label_text, items, item_w, item_h, draw_item):
            """A labelled vertical stack, falling back to no label."""
            n = len(items)
            body_h = n * (item_h + 1) - 1

            def draw(with_label):
                def _draw(x, y):
                    if with_label:
                        self._text(x, y, label_text, LABEL)
                        y += lh + 1
                    for item in items:
                        draw_item(x, y, item)
                        y += item_h + 1
                return _draw

            return [
                (max(font.text_width(label_text), item_w), lh + 1 + body_h, draw(True)),
                (item_w, body_h, draw(False)),
            ]

        def stat(label_text, text, color):
            """A label over a value, falling back to the value alone."""
            lw = font.text_width(label_text)
            vw = font.text_width(text)

            def with_label(x, y):
                self._text(x, y, label_text, LABEL)
                self._text(x, y + lh + 1, text, color)

            def bare(x, y):
                self._text(x, y, text, color)

            return [
                (max(lw, vw), lh * 2 + 1, with_label),
                (vw, lh, bare),
            ]

        packer.place_first(stack(
            "NEXT", game.queue[:game.preview], pw, ph,
            lambda x, y, name: self._piece_preview(x, y, name, ps)))
        packer.place_first(stack(
            "HOLD", [game.hold], pw, ph,
            lambda x, y, name: self._piece_preview(
                x, y, name, ps, factor=0.3 if game.hold_used else 1.0)))

        widest = lay._widest_zone()
        max_chars = max(1, (widest + 1) // (font.GLYPH_W + 1))
        score = compact(game.score, max_chars)
        lines = compact(game.lines, max_chars)
        level = compact(game.level, max_chars)
        stats = ((score, VALUE), (lines, LINES_COLOR), (level, LEVEL_COLOR))

        lv_line = "LV %s" % level

        def labelled_column(x, y):
            for text, color, label_text in ((score, VALUE, "SCORE"),
                                            (lines, LINES_COLOR, "LINES")):
                self._text(x, y, label_text, LABEL)
                self._text(x, y + lh + 1, text, color)
                y += lh * 2 + 2
            self._text(x, y, lv_line, LEVEL_COLOR)

        def bare_column(x, y):
            for text, color in stats:
                self._text(x, y, text, color)
                y += lh + 1

        def single_row(gap):
            def _draw(x, y):
                for text, color in stats:
                    self._text(x, y, text, color)
                    x += font.text_width(text) + gap
            return _draw

        def row_width(gap):
            return sum(font.text_width(t) for t, _ in stats) + gap * 2

        col_w = max(font.text_width(t) for t in (score, lines, lv_line, "SCORE"))
        bare_w = max(font.text_width(t) for t, _ in stats)
        packer.place_first([
            (col_w, (lh * 2 + 2) * 2 + lh, labelled_column),
            (row_width(2), lh, single_row(2)),
            (row_width(1), lh, single_row(1)),
            (bare_w, lh * 3 + 2, bare_column),
            (font.text_width(score), lh, lambda x, y: self._text(x, y, score, VALUE)),
        ])

def compact(value, max_chars):
    """Render an integer in at most `max_chars` glyphs, using K/M if needed."""
    s = str(int(value))
    if len(s) <= max_chars:
        return s
    for div, suffix in ((10 ** 3, "K"), (10 ** 6, "M"), (10 ** 9, "G")):
        scaled = int(value) // div
        if scaled < 1:
            break
        t = "%d%s" % (scaled, suffix)
        if len(t) <= max_chars:
            return t
    return "9" * max_chars
