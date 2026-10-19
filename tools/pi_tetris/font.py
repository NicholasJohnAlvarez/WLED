"""A 3x5 pixel font - just enough for a HUD on a small LED matrix."""

GLYPH_W = 3
GLYPH_H = 5
_ADVANCE = GLYPH_W + 1  # one blank column between glyphs

_RAW = {
    "0": "###;#.#;#.#;#.#;###",
    "1": ".#.;##.;.#.;.#.;###",
    "2": "###;..#;###;#..;###",
    "3": "###;..#;###;..#;###",
    "4": "#.#;#.#;###;..#;..#",
    "5": "###;#..;###;..#;###",
    "6": "###;#..;###;#.#;###",
    "7": "###;..#;..#;..#;..#",
    "8": "###;#.#;###;#.#;###",
    "9": "###;#.#;###;..#;###",
    "A": "###;#.#;###;#.#;#.#",
    "B": "##.;#.#;##.;#.#;##.",
    "C": "###;#..;#..;#..;###",
    "D": "##.;#.#;#.#;#.#;##.",
    "E": "###;#..;##.;#..;###",
    "F": "###;#..;##.;#..;#..",
    "G": "###;#..;#.#;#.#;###",
    "H": "#.#;#.#;###;#.#;#.#",
    "I": "###;.#.;.#.;.#.;###",
    "J": "..#;..#;..#;#.#;###",
    "K": "#.#;#.#;##.;#.#;#.#",
    "L": "#..;#..;#..;#..;###",
    "M": "#.#;###;###;#.#;#.#",
    "N": "##.;#.#;#.#;#.#;#.#",
    "O": "###;#.#;#.#;#.#;###",
    "P": "###;#.#;###;#..;#..",
    "Q": "###;#.#;###;..#;..#",
    "R": "###;#.#;##.;#.#;#.#",
    "S": "###;#..;###;..#;###",
    "T": "###;.#.;.#.;.#.;.#.",
    "U": "#.#;#.#;#.#;#.#;###",
    "V": "#.#;#.#;#.#;#.#;.#.",
    "W": "#.#;#.#;###;###;#.#",
    "X": "#.#;#.#;.#.;#.#;#.#",
    "Y": "#.#;#.#;###;.#.;.#.",
    "Z": "###;..#;.#.;#..;###",
    " ": "...;...;...;...;...",
    "-": "...;...;###;...;...",
    ".": "...;...;...;...;.#.",
    ":": "...;.#.;...;.#.;...",
    "!": ".#.;.#.;.#.;...;.#.",
    "?": "###;..#;.##;...;.#.",
    "*": "#.#;.#.;###;.#.;#.#",
}

# {char: tuple of (x, y) lit pixels}
GLYPHS = {
    ch: tuple(
        (x, y)
        for y, row in enumerate(rows.split(";"))
        for x, cell in enumerate(row)
        if cell == "#"
    )
    for ch, rows in _RAW.items()
}


def text_width(text, scale=1, spacing=1):
    """Pixel width of `text`, excluding the trailing inter-glyph gap."""
    if not text:
        return 0
    return (len(text) * (GLYPH_W + spacing) - spacing) * scale


def text_height(scale=1):
    return GLYPH_H * scale


def fits(text, max_width, scale=1, spacing=1):
    return text_width(text, scale, spacing) <= max_width


def draw_text(canvas, x, y, text, color, scale=1, spacing=1):
    """Blit `text` onto anything with a .set(x, y, color) method."""
    step = (GLYPH_W + spacing) * scale
    for i, ch in enumerate(text.upper()):
        glyph = GLYPHS.get(ch)
        if not glyph:
            continue
        gx = x + i * step
        for px, py in glyph:
            if scale == 1:
                canvas.set(gx + px, y + py, color)
            else:
                for dy in range(scale):
                    for dx in range(scale):
                        canvas.set(gx + px * scale + dx, y + py * scale + dy, color)
    return x + len(text) * step - spacing * scale
