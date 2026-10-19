"""A simple heuristic player, used for --demo (attract mode / smoke test).

Scores every landing position of the current piece on four classic features -
aggregate height, completed lines, holes and surface bumpiness - and walks the
piece there. Good enough to clear hundreds of lines, which makes it a decent
way to confirm wiring, orientation and throughput without a controller.
"""

import tetris as T

W_HEIGHT = -0.51
W_LINES = 0.76
W_HOLES = -0.36
W_BUMPINESS = -0.18


def _column_profile(board, cols, rows):
    heights = [0] * cols
    holes = 0
    for x in range(cols):
        seen = False
        for y in range(rows):
            if board[y][x]:
                if not seen:
                    heights[x] = rows - y
                    seen = True
            elif seen:
                holes += 1
    return heights, holes


def _evaluate(board, cols, rows, cleared):
    heights, holes = _column_profile(board, cols, rows)
    bumpiness = sum(abs(heights[i] - heights[i + 1]) for i in range(cols - 1))
    return (W_HEIGHT * sum(heights) + W_LINES * cleared
            + W_HOLES * holes + W_BUMPINESS * bumpiness)


def best_placement(game):
    """Returns (rotation, x) for the current piece, or None."""
    piece = game.piece
    if piece is None:
        return None
    cols, rows = game.cols, game.rows
    best = None
    seen_shapes = set()
    for rot in range(4):
        shape = T.SHAPES[piece.name][rot]
        if shape in seen_shapes:
            continue
        seen_shapes.add(shape)
        width = max(cx for cx, _ in shape) + 1
        for x in range(-min(cx for cx, _ in shape), cols - width + 2):
            cells = [(x + cx, cy) for cx, cy in shape]
            if any(cx < 0 or cx >= cols for cx, _ in cells):
                continue
            y = piece.y
            if game._collides([(cx, cy + y) for cx, cy in cells]):
                continue
            while not game._collides([(cx, cy + y + 1) for cx, cy in cells]):
                y += 1
            landed = [(cx, cy + y) for cx, cy in cells]
            if any(cy < 0 for _, cy in landed):
                continue
            board = [row[:] for row in game.board]
            for cx, cy in landed:
                board[cy][cx] = piece.pid
            cleared = sum(1 for row in board if all(row))
            if cleared:
                board = ([[0] * cols for _ in range(cleared)]
                         + [row for row in board if not all(row)])
            score = _evaluate(board, cols, rows, cleared)
            if best is None or score > best[0]:
                best = (score, rot, x)
    return (best[1], best[2]) if best else None


class Bot:
    def __init__(self, move_interval=0.05):
        self.move_interval = move_interval
        self._target = None
        self._piece = None
        self._next_move = 0.0

    def actions(self, game, now):
        if game.state == T.GAME_OVER:
            return [T.RESTART]
        if game.state != T.PLAYING or game.piece is None:
            return []
        token = (id(game.piece), game.piece.name)
        if token != self._piece:
            self._piece = token
            self._target = best_placement(game)
        if self._target is None or now < self._next_move:
            return []
        self._next_move = now + self.move_interval
        rot, x = self._target
        piece = game.piece
        if piece.rot != rot:
            return [T.ROT_CW]
        if piece.x < x:
            return [T.RIGHT]
        if piece.x > x:
            return [T.LEFT]
        return [T.HARD_DROP]
