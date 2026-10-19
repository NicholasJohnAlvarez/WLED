"""Canvas that maps game coordinates onto a WLED 2D matrix.

WLED's realtime path writes the *logical* pixel buffer and applies the 2D
panel ledmap at show time (FX.h, getMappedPixelIndex), so a DDP pixel index of
`y * panel_width + x` is all this side needs - serpentine wiring, panel order
and the split across output channels are already handled on the controller.

Rotation is resolved once into a lookup table of byte offsets, so drawing a
pixel is a bounds check and a 3-byte slice assignment.
"""

BLACK = b"\x00\x00\x00"


def rgb(r, g, b):
    return bytes((max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b)))))


def dim(color, factor):
    return rgb(color[0] * factor, color[1] * factor, color[2] * factor)


class Canvas:
    def __init__(self, panel_width, panel_height, rotate=0, flip_x=False,
                 flip_y=False, ledmap=None):
        if rotate not in (0, 90, 180, 270):
            raise ValueError("rotate must be 0, 90, 180 or 270")
        self.panel_width = panel_width
        self.panel_height = panel_height
        self.pixel_count = panel_width * panel_height
        self.rotate = rotate
        if rotate in (90, 270):
            self.width, self.height = panel_height, panel_width
        else:
            self.width, self.height = panel_width, panel_height

        self._buf = bytearray(self.pixel_count * 3)
        self._idx = self._build_index(flip_x, flip_y, ledmap)

    def _build_index(self, flip_x, flip_y, ledmap):
        pw, ph = self.panel_width, self.panel_height
        idx = [-1] * (self.width * self.height)
        for cy in range(self.height):
            for cx in range(self.width):
                if self.rotate == 0:
                    px, py = cx, cy
                elif self.rotate == 90:
                    px, py = pw - 1 - cy, cx
                elif self.rotate == 180:
                    px, py = pw - 1 - cx, ph - 1 - cy
                else:  # 270
                    px, py = cy, ph - 1 - cx
                if flip_x:
                    px = pw - 1 - px
                if flip_y:
                    py = ph - 1 - py
                logical = py * pw + px
                target = logical if ledmap is None else ledmap[logical]
                idx[cx + cy * self.width] = -1 if target < 0 else target * 3
        return idx

    # --- drawing ---------------------------------------------------------

    def clear(self):
        self._buf[:] = bytes(len(self._buf))

    def set(self, x, y, color):
        if 0 <= x < self.width and 0 <= y < self.height:
            off = self._idx[x + y * self.width]
            if off >= 0:
                self._buf[off:off + 3] = color

    def fill_rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.set(xx, yy, color)

    def rect(self, x, y, w, h, color):
        """A one-pixel outline around the given rectangle."""
        for xx in range(x, x + w):
            self.set(xx, y, color)
            self.set(xx, y + h - 1, color)
        for yy in range(y + 1, y + h - 1):
            self.set(x, yy, color)
            self.set(x + w - 1, yy, color)

    def buffer(self):
        return self._buf

    # --- debugging -------------------------------------------------------

    def to_ascii(self):
        """Half-block terminal preview of the panel (two rows per text line)."""
        pw = self.panel_width
        out = []
        for py in range(0, self.panel_height, 2):
            line = []
            for px in range(pw):
                top = self._buf[(py * pw + px) * 3:(py * pw + px) * 3 + 3]
                if py + 1 < self.panel_height:
                    o = ((py + 1) * pw + px) * 3
                    bot = self._buf[o:o + 3]
                else:
                    bot = BLACK
                t, b = any(top), any(bot)
                line.append("█" if t and b else "▀" if t else "▄" if b else " ")
            out.append("".join(line))
        return "\n".join(out)


def load_ledmap(path):
    """Load a WLED ledmap JSON file ({"map": [...]} or a bare array)."""
    import json
    with open(path) as fh:
        data = json.load(fh)
    table = data["map"] if isinstance(data, dict) else data
    return [-1 if v is None or v < 0 else int(v) for v in table]
