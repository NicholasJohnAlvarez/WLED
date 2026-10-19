#!/usr/bin/env python3
"""Tests for the Pi-side Tetris game. Run: python3 -m unittest discover -s tools/pi_tetris

The DDP test reimplements WLED's own receiver (wled00/e131.cpp,
handleDDPPacket) so the packets this tool sends are checked against the
parser that will actually consume them.
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot  # noqa: E402
import controls  # noqa: E402
import ddp  # noqa: E402
import font  # noqa: E402
import renderer as rend  # noqa: E402
import tetris as T  # noqa: E402
from canvas import Canvas, rgb  # noqa: E402


class FakeSocket:
    def __init__(self):
        self.packets = []

    def setsockopt(self, *_a):
        pass

    def sendto(self, data, _addr):
        self.packets.append(bytes(data))

    def close(self):
        pass


def wled_receive(packets, pixel_count):
    """Decode DDP packets the way wled00/e131.cpp does.

    Returns (pixels, pushes) where pixels is a flat RGB bytearray.
    """
    pixels = bytearray(pixel_count * 3)
    pushes = 0
    for packet in packets:
        flags, _seq, data_type, dest, channel_offset, data_len = struct.unpack_from(
            ">BBBBIH", packet, 0)
        assert dest == 1, "destination must be DDP_ID_DISPLAY"
        assert len(packet) <= 1458, "larger than WLED's receive buffer"
        channels_per_led = 4 if (data_type & 0b00111000) >> 3 == 0b011 else 3
        data = packet[10:]
        start = channel_offset // channels_per_led
        # WLED adds DMXAddress / channels_per_led; the default DMXAddress of 1
        # is a no-op under integer division.
        stop = start + data_len // channels_per_led
        c = 4 if flags & 0x10 else 0
        if c + (stop - start) * channels_per_led > data_len:
            raise AssertionError("WLED would reject this packet (bounds)")
        for i in range(start, stop):
            pixels[i * 3:i * 3 + 3] = data[c:c + 3]
            c += channels_per_led
        if flags & 0x01:
            pushes += 1
    return pixels, pushes


class TestDDP(unittest.TestCase):
    def _send(self, frame):
        sender = ddp.DDPSender("127.0.0.1")
        sock = FakeSocket()
        sender._sock = sock
        sender.send(frame)
        return sock.packets

    def test_frame_survives_wleds_parser(self):
        count = 48 * 32
        frame = bytes((i * 7) % 256 for i in range(count * 3))
        packets = self._send(frame)
        self.assertEqual(len(packets), 4)  # 1440 + 1440 + 1440 + 288 bytes
        received, pushes = wled_receive(packets, count)
        self.assertEqual(bytes(received), frame)
        self.assertEqual(pushes, 1, "exactly one PUSH, on the last packet")

    def test_only_last_packet_pushes(self):
        packets = self._send(bytes(48 * 32 * 3))
        pushed = [bool(p[0] & 0x01) for p in packets]
        self.assertEqual(pushed, [False, False, False, True])

    def test_header_fields(self):
        packets = self._send(bytes(30 * 3))
        flags, seq, data_type, dest, offset, length = struct.unpack_from(
            ">BBBBIH", packets[0], 0)
        self.assertEqual(flags, 0x41)            # version 1 + push
        self.assertEqual(data_type, 0x0B)        # RGB24, 3 channels
        self.assertEqual(dest, 1)                # DDP_ID_DISPLAY
        self.assertEqual(offset, 0)
        self.assertEqual(length, 90)
        self.assertEqual(len(packets[0]), 100)
        self.assertIn(seq, range(1, 16))

    def test_sequence_advances_once_per_frame_and_wraps(self):
        sender = ddp.DDPSender("127.0.0.1")
        sender._sock = sock = FakeSocket()
        seen = []
        for _ in range(17):
            sock.packets.clear()
            sender.send(bytes(48 * 32 * 3))
            frame_seqs = {p[1] for p in sock.packets}
            self.assertEqual(len(frame_seqs), 1, "one sequence number per frame")
            seen.append(frame_seqs.pop())
        self.assertEqual(seen[:15], list(range(1, 16)))
        self.assertEqual(seen[15], 1, "wraps to 1, never 0")
        self.assertNotIn(0, seen)

    def test_payload_never_exceeds_wled_buffer(self):
        packets = self._send(bytes(4096 * 3))
        for packet in packets:
            self.assertLessEqual(len(packet), 1458)
            self.assertEqual((len(packet) - 10) % 3, 0)


class TestCanvas(unittest.TestCase):
    def test_index_is_row_major(self):
        c = Canvas(48, 32)
        c.set(5, 3, rgb(1, 2, 3))
        index = 3 * 48 + 5
        self.assertEqual(bytes(c.buffer()[index * 3:index * 3 + 3]), b"\x01\x02\x03")

    def test_rotations_are_bijective(self):
        for rotate in (0, 90, 180, 270):
            for flip in ((False, False), (True, False), (False, True), (True, True)):
                c = Canvas(48, 32, rotate=rotate, flip_x=flip[0], flip_y=flip[1])
                offsets = [o for o in c._idx if o >= 0]
                self.assertEqual(len(offsets), 48 * 32)
                self.assertEqual(len(set(offsets)), 48 * 32)

    def test_rotate_swaps_canvas_dimensions(self):
        self.assertEqual((Canvas(48, 32, rotate=90).width,
                          Canvas(48, 32, rotate=90).height), (32, 48))

    def test_out_of_bounds_writes_are_dropped(self):
        c = Canvas(16, 16)
        c.set(-1, 0, rgb(255, 255, 255))
        c.set(16, 0, rgb(255, 255, 255))
        c.set(0, 99, rgb(255, 255, 255))
        self.assertEqual(bytes(c.buffer()), bytes(16 * 16 * 3))

    def test_ledmap_is_applied(self):
        c = Canvas(4, 1, ledmap=[3, 2, 1, 0])
        c.set(0, 0, rgb(9, 9, 9))
        self.assertEqual(bytes(c.buffer()[9:12]), b"\x09\x09\x09")
        self.assertEqual(bytes(c.buffer()[0:3]), b"\x00\x00\x00")


class TestEngine(unittest.TestCase):
    def test_srs_spawn_and_quarter_turns(self):
        self.assertEqual(T.SHAPES["T"][0], tuple(sorted(((1, 0), (0, 1), (1, 1), (2, 1)))))
        self.assertEqual(T.SHAPES["T"][1], tuple(sorted(((1, 0), (1, 1), (2, 1), (1, 2)))))
        self.assertEqual(T.SHAPES["I"][1], tuple(sorted(((2, 0), (2, 1), (2, 2), (2, 3)))))
        self.assertEqual(T.SHAPES["O"][0], T.SHAPES["O"][2])
        for name in T.ORDER:
            for state in T.SHAPES[name]:
                self.assertEqual(len(set(state)), 4)

    def test_wall_kick_rotates_i_off_the_left_wall(self):
        g = T.Tetris(seed=1)
        g._spawn(0.0, "I")
        g.action(T.ROT_CW, 0.0)
        for _ in range(10):
            g.action(T.LEFT, 0.0)
        self.assertEqual(min(x for x, _ in g.piece.cells()), 0)
        g.action(T.ROT_CW, 0.0)  # back to horizontal, needs a kick to fit
        self.assertEqual(g.piece.rot, 2)
        self.assertGreaterEqual(min(x for x, _ in g.piece.cells()), 0)

    def test_180_rotation_does_not_leave_a_quarter_turn(self):
        g = T.Tetris(seed=1)
        g._spawn(0.0, "T")
        for y in range(g.rows):
            g.board[y][4] = 1  # block the column the nub would sweep through
        before = g.piece.rot
        g.action(T.ROT_180, 0.0)
        self.assertIn(g.piece.rot, (before, (before + 2) % 4))

    def test_line_clear_scores_and_collapses(self):
        g = T.Tetris(seed=5)
        for x in range(1, 10):
            g.board[19][x] = 1
        g._spawn(0.0, "I")
        g.action(T.ROT_CW, 0.0)
        for _ in range(10):
            g.action(T.LEFT, 0.0)
        g.action(T.HARD_DROP, 0.0)
        self.assertEqual(g.state, T.CLEARING)
        self.assertEqual(g.clear_rows, [19])
        g.update(T.CLEAR_FLASH + 0.01)
        self.assertEqual(g.state, T.PLAYING)
        self.assertEqual(g.lines, 1)
        self.assertGreaterEqual(g.score, 100)
        self.assertEqual([y for y in range(20) if g.board[y][0]], [17, 18, 19])

    def test_tetris_scores_800_times_level(self):
        g = T.Tetris(seed=5, start_level=2)
        for y in range(16, 20):
            for x in range(1, 10):
                g.board[y][x] = 1
        g._spawn(0.0, "I")
        g.action(T.ROT_CW, 0.0)
        for _ in range(10):
            g.action(T.LEFT, 0.0)
        before = g.score
        dropped = g.ghost_y() - g.piece.y
        g.action(T.HARD_DROP, 0.0)
        self.assertEqual(len(g.clear_rows), 4)
        g.update(T.CLEAR_FLASH + 0.01)
        # 800 per tetris x level 2, plus 2 points per hard-dropped row
        self.assertEqual(g.score - before, 800 * 2 + 2 * dropped)

    def test_hold_is_once_per_piece(self):
        g = T.Tetris(seed=5)
        first = g.piece.name
        g.action(T.HOLD, 0.0)
        self.assertEqual(g.hold, first)
        self.assertNotEqual(g.piece.name, first)
        swapped = g.piece.name
        g.action(T.HOLD, 0.0)
        self.assertEqual(g.piece.name, swapped)

    def test_seven_bag_gives_each_piece_once_per_bag(self):
        g = T.Tetris(seed=9)
        drawn = [g.piece.name] + list(g.queue)
        while len(drawn) < 28:
            drawn.append(g._next_name())
        for start in range(0, 28, 7):
            self.assertEqual(sorted(drawn[start:start + 7]), sorted(T.ORDER))

    def test_game_over_on_blocked_spawn(self):
        g = T.Tetris(seed=5)
        g.board = [[1] * 10 for _ in range(20)]
        g._spawn(0.0)
        self.assertEqual(g.state, T.GAME_OVER)

    def test_gravity_speeds_up_with_level(self):
        self.assertAlmostEqual(T.gravity_for(1), 1.0)
        self.assertLess(T.gravity_for(10), T.gravity_for(5))
        self.assertLess(T.gravity_for(15), T.gravity_for(10))
        self.assertGreater(T.gravity_for(30), 0.0)

    def test_lock_delay_then_lock(self):
        g = T.Tetris(seed=5)
        g._spawn(0.0, "O")
        while g._try_move(0, 1):
            pass
        g.update(0.1)
        self.assertIsNotNone(g.lock_start)
        self.assertIsNotNone(g.piece)
        g.update(0.1 + T.LOCK_DELAY + 0.01)
        self.assertTrue(any(any(row) for row in g.board))

    def test_pause_blocks_play_and_resumes(self):
        g = T.Tetris(seed=5)
        g.action(T.PAUSE, 0.0)
        self.assertEqual(g.state, T.PAUSED)
        x = g.piece.x
        g.action(T.LEFT, 0.0)
        self.assertEqual(g.piece.x, x)
        g.action(T.PAUSE, 0.0)
        self.assertEqual(g.state, T.PLAYING)

    def test_random_play_keeps_the_board_valid(self):
        import random
        g = T.Tetris(seed=7)
        rnd = random.Random(3)
        acts = [T.LEFT, T.RIGHT, T.SOFT_DROP, T.HARD_DROP, T.ROT_CW, T.ROT_CCW,
                T.ROT_180, T.HOLD]
        now = 0.0
        for _ in range(5000):
            now += 0.016
            if rnd.random() < 0.4:
                g.action(rnd.choice(acts), now)
            g.update(now, soft_dropping=rnd.random() < 0.2)
            if g.state == T.GAME_OVER:
                g.reset(now)
            self.assertEqual(len(g.board), 20)
            self.assertTrue(all(len(r) == 10 for r in g.board))
            if g.piece:
                for x, y in g.piece.cells():
                    self.assertTrue(0 <= x < 10)
                    self.assertLess(y, 20)


class TestRenderer(unittest.TestCase):
    def test_layout_for_a_48x32_panel(self):
        lay = rend.Layout(48, 32, 10, 20)
        self.assertEqual(lay.scale, 1)
        self.assertTrue(lay.wide)
        self.assertTrue(lay.fits)

    def test_layout_for_the_same_panel_mounted_portrait(self):
        lay = rend.Layout(32, 48, 10, 20)
        self.assertEqual(lay.scale, 2)
        self.assertFalse(lay.wide)
        self.assertTrue(lay.fits)
        self.assertEqual(len(lay.zones), 2)  # side column plus bottom strip

    def test_hud_stays_inside_the_canvas(self):
        """Canvas.set clips silently, so count the writes it had to drop."""

        class StrictCanvas(Canvas):
            clipped = 0

            def set(self, x, y, color):
                if not (0 <= x < self.width and 0 <= y < self.height):
                    StrictCanvas.clipped += 1
                Canvas.set(self, x, y, color)

        for panel in ((48, 32), (32, 48), (64, 64), (96, 48)):
            StrictCanvas.clipped = 0
            c = StrictCanvas(*panel)
            g = T.Tetris(seed=2)
            g.score, g.lines, g.level, g.hold = 1234567, 342, 17, "T"
            rend.Renderer(c, 10, 20).render(g, 0.0)
            self.assertEqual(StrictCanvas.clipped, 0, panel)

    def test_a_playfield_too_large_for_the_panel_is_flagged(self):
        lay = rend.Layout(16, 16, 10, 20)
        self.assertFalse(lay.fits)

    def test_every_state_renders(self):
        c = Canvas(48, 32)
        g = T.Tetris(seed=2)
        r = rend.Renderer(c, 10, 20)
        for state in (T.PLAYING, T.PAUSED, T.CLEARING, T.GAME_OVER):
            g.state = state
            g.clear_rows = [19] if state == T.CLEARING else []
            r.render(g, 5.0)
            self.assertTrue(any(c.buffer()), state)

    def test_compact_numbers(self):
        self.assertEqual(rend.compact(42, 5), "42")
        self.assertEqual(rend.compact(1234567, 5), "1234K")
        self.assertEqual(rend.compact(99999999, 2), "99")
        self.assertEqual(rend.compact(0, 1), "0")


class TestFont(unittest.TestCase):
    def test_every_glyph_is_3x5(self):
        for ch, rows in font._RAW.items():
            parts = rows.split(";")
            self.assertEqual(len(parts), font.GLYPH_H, ch)
            for row in parts:
                self.assertEqual(len(row), font.GLYPH_W, ch)

    def test_digits_and_letters_present(self):
        for ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            self.assertIn(ch, font.GLYPHS)

    def test_width(self):
        self.assertEqual(font.text_width("SCORE"), 19)
        self.assertEqual(font.text_width(""), 0)


class TestControls(unittest.TestCase):
    def test_event_struct_matches_the_kernels(self):
        # struct input_event: struct timeval + __u16 + __u16 + __s32
        expected = 24 if struct.calcsize("l") == 8 else 16
        self.assertEqual(controls._EVENT_SIZE, expected)

    def test_ioctl_numbers(self):
        self.assertEqual(controls._EVIOCGNAME, 0x81004506)
        self.assertEqual(controls._EVIOCGRAB, 0x40044590)
        self.assertEqual(controls._EVIOCGABS(0), 0x80184540)

    def test_das_then_arr(self):
        r = controls.Repeater(das=0.17, arr=0.04)
        self.assertEqual(r.feed([(T.LEFT, True)], 0.0), [T.LEFT])
        self.assertEqual(r.feed([], 0.10), [])
        self.assertEqual(r.feed([], 0.17), [T.LEFT])
        self.assertEqual(r.feed([], 0.22), [T.LEFT])
        self.assertEqual(r.feed([(T.LEFT, False)], 0.25), [])
        self.assertEqual(r.feed([], 0.50), [])

    def test_opposite_direction_takes_over_then_falls_back(self):
        r = controls.Repeater()
        r.feed([(T.LEFT, True)], 0.0)
        self.assertEqual(r.feed([(T.RIGHT, True)], 0.01), [T.RIGHT])
        self.assertEqual(r._shift, T.RIGHT)
        r.feed([(T.RIGHT, False)], 0.02)
        self.assertEqual(r._shift, T.LEFT, "left is still held")

    def test_soft_drop_is_a_held_state(self):
        r = controls.Repeater()
        r.feed([(T.SOFT_DROP, True)], 0.0)
        self.assertTrue(r.soft_dropping)
        r.feed([(T.SOFT_DROP, False)], 0.1)
        self.assertFalse(r.soft_dropping)

    def test_default_mapping_covers_every_action(self):
        keys, axes = controls.load_mapping()
        mapped = set(keys.values()) | {a for pair in axes.values() for a in pair}
        for action in (T.LEFT, T.RIGHT, T.SOFT_DROP, T.HARD_DROP, T.ROT_CW,
                       T.ROT_CCW, T.ROT_180, T.HOLD, T.PAUSE, T.RESTART, T.QUIT):
            self.assertIn(action, mapped)

    def test_axis_events_emit_release_then_press(self):
        device = controls.EvdevDevice.__new__(controls.EvdevDevice)
        device._axes = {controls.ABS_HAT0X: (T.LEFT, T.RIGHT)}
        device._axis_state = {}
        device._thresholds = {controls.ABS_HAT0X: (0.0, 0.5)}
        self.assertEqual(device._axis_events(controls.ABS_HAT0X, -1),
                         [(T.LEFT, True)])
        self.assertEqual(device._axis_events(controls.ABS_HAT0X, 1),
                         [(T.LEFT, False), (T.RIGHT, True)])
        self.assertEqual(device._axis_events(controls.ABS_HAT0X, 0),
                         [(T.RIGHT, False)])
        self.assertEqual(device._axis_events(controls.ABS_HAT0X, 0), [])


class TestBot(unittest.TestCase):
    def test_bot_clears_lines(self):
        g = T.Tetris(seed=4)
        b = bot.Bot(move_interval=0.0)
        now = 0.0
        for _ in range(200000):
            now += 0.004
            for action in b.actions(g, now):
                g.action(action, now)
            g.update(now)
            if g.lines >= 20:
                break
        self.assertGreaterEqual(g.lines, 20)
        self.assertNotEqual(g.state, T.GAME_OVER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
