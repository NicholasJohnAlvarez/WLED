#!/usr/bin/env python3
"""Real-time Tetris on a WLED matrix, driven over DDP from a Raspberry Pi.

    ./run.py --host 192.168.1.50 --panel 48x32 --demo
    ./run.py --host 192.168.1.50 --panel 48x32

WLED settings that matter (Config -> Sync Interfaces):
  * "Respect LED Maps" ON  - so this side can address pixels as y*width + x
  * "Use main segment only" OFF
  * "Realtime timeout" >= 2500 ms (the default is fine)
  * "Force max brightness"/gamma are up to you; see --brightness and --gamma

Run with --test-pattern first to confirm orientation before playing.
"""

import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import controls  # noqa: E402
import font  # noqa: E402
import tetris as T  # noqa: E402
from bot import Bot  # noqa: E402
from canvas import Canvas, load_ledmap, rgb  # noqa: E402
from ddp import DDPSender  # noqa: E402
from renderer import Renderer  # noqa: E402


def parse_panel(text):
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError("panel must look like 48x32")


def build_lut(brightness, gamma):
    if brightness == 1.0 and gamma == 1.0:
        return None
    return bytes(min(255, int(round(255.0 * ((i / 255.0) ** gamma) * brightness)))
                 for i in range(256))


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Real-time Tetris on a WLED matrix over DDP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--host", help="WLED IP address or hostname")
    p.add_argument("--port", type=int, default=4048, help="DDP port (default 4048)")
    p.add_argument("--panel", type=parse_panel, default=(48, 32),
                   help="matrix size as configured in WLED, e.g. 48x32")
    p.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=0,
                   help="rotate the game to suit how the panel is mounted")
    p.add_argument("--flip-x", action="store_true")
    p.add_argument("--flip-y", action="store_true")
    p.add_argument("--ledmap", help="map pixels here instead of in WLED "
                                    "(then turn Respect LED Maps OFF)")
    p.add_argument("--cols", type=int, default=10, help="playfield width in cells")
    p.add_argument("--rows", type=int, default=20, help="playfield height in cells")
    p.add_argument("--level", type=int, default=1, help="starting level")
    p.add_argument("--preview-count", type=int, default=3, help="next-piece previews")
    p.add_argument("--fps", type=float, default=60.0,
                   help="frames per second (try 30-40 on a Pi Zero W)")
    p.add_argument("--brightness", type=float, default=1.0,
                   help="0..1 scale applied before sending")
    p.add_argument("--gamma", type=float, default=1.0,
                   help="gamma applied before sending (WLED skips gamma on "
                        "realtime data by default)")
    p.add_argument("--no-ghost", action="store_true", help="hide the ghost piece")
    p.add_argument("--seed", type=int, help="fixed piece sequence")
    p.add_argument("--device", action="append", metavar="PATH",
                   help="/dev/input/eventN to read (repeatable; default: all)")
    p.add_argument("--keyboard", action="store_true",
                   help="also read this terminal (for SSH; taps only, no DAS)")
    p.add_argument("--grab", action="store_true",
                   help="claim input devices exclusively so keys do not reach "
                        "the console as well")
    p.add_argument("--controls", help="controls.json from --map-controller")
    p.add_argument("--demo", action="store_true", help="let the bot play")
    p.add_argument("--preview", action="store_true",
                   help="mirror the panel in the terminal as text")
    p.add_argument("--stats", action="store_true", help="print fps once a second")
    p.add_argument("--dry-run", action="store_true", help="render but send nothing")
    p.add_argument("--test-pattern", action="store_true",
                   help="show an orientation test pattern and exit on Ctrl-C")
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--map-controller", action="store_true",
                   help="record your controller's buttons and save them")
    return p.parse_args(argv)


def make_canvas(args):
    ledmap = load_ledmap(args.ledmap) if args.ledmap else None
    return Canvas(args.panel[0], args.panel[1], rotate=args.rotate,
                  flip_x=args.flip_x, flip_y=args.flip_y, ledmap=ledmap)


def draw_test_pattern(canvas):
    """Corner markers plus labels, so orientation can be checked at a glance."""
    canvas.clear()
    w, h = canvas.width, canvas.height
    canvas.rect(0, 0, w, h, rgb(0, 0, 40))
    canvas.fill_rect(0, 0, 2, 2, rgb(255, 0, 0))            # top left
    canvas.fill_rect(w - 2, 0, 2, 2, rgb(0, 255, 0))        # top right
    canvas.fill_rect(0, h - 2, 2, 2, rgb(0, 0, 255))        # bottom left
    canvas.fill_rect(w - 2, h - 2, 2, 2, rgb(255, 255, 255))  # bottom right
    label = "%dX%d" % (w, h)
    if font.text_width(label) + 4 <= w:
        font.draw_text(canvas, 3, 3, label, rgb(120, 120, 120))
    if font.text_width("TOP") + 4 <= w:
        font.draw_text(canvas, 3, 9, "TOP", rgb(200, 120, 0))
    for x in range(w):
        canvas.set(x, h // 2, rgb(x * 255 // max(1, w - 1), 0, 0))
    for y in range(h):
        canvas.set(w // 2, y, rgb(0, y * 255 // max(1, h - 1), 0))


def main(argv=None):
    args = parse_args(argv)

    if args.list_devices:
        devices = controls.list_devices()
        if not devices:
            print("no readable /dev/input/event* devices "
                  "(try sudo, or: sudo usermod -aG input $USER)")
        for path, name in devices:
            print("%-20s %s" % (path, name))
        return 0

    if args.map_controller:
        controls.map_controller(args.device)
        return 0

    if not args.host and not args.dry_run:
        print("error: --host is required (or use --dry-run)", file=sys.stderr)
        return 2

    canvas = make_canvas(args)
    lut = build_lut(args.brightness, args.gamma)
    sender = None if args.dry_run else DDPSender(args.host, args.port)
    hub = None

    stop = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_a: stop.append(True))

    def push():
        buf = canvas.buffer()
        if lut:
            buf = buf.translate(lut)
        if sender:
            sender.send(buf)

    try:
        if args.test_pattern:
            draw_test_pattern(canvas)
            print("test pattern: red=top-left green=top-right "
                  "blue=bottom-left white=bottom-right. Ctrl-C to stop.")
            if args.preview:
                print(canvas.to_ascii())
            while not stop:
                push()
                time.sleep(0.5)
            return 0

        game = T.Tetris(cols=args.cols, rows=args.rows, start_level=args.level,
                        seed=args.seed, preview=args.preview_count)
        renderer = Renderer(canvas, args.cols, args.rows, show_ghost=not args.no_ghost)
        if not renderer.layout.fits:
            print("warning: a %dx%d playfield does not fit a %dx%d canvas; "
                  "it will be clipped" % (args.cols, args.rows,
                                          canvas.width, canvas.height),
                  file=sys.stderr)

        bot = Bot() if args.demo else None
        repeater = controls.Repeater()
        if not args.demo:
            hub = controls.InputHub(args.device, use_terminal=args.keyboard,
                                    mapping=args.controls, grab=args.grab)
            print("input: %s" % hub.describe())
            if hub.describe() == "none":
                print("no input devices; try --list-devices, --keyboard or --demo",
                      file=sys.stderr)

        frame = 1.0 / max(1.0, args.fps)
        next_frame = time.monotonic()
        frames = 0
        last_report = time.monotonic()
        game.reset(time.monotonic())

        while not stop:
            now = time.monotonic()
            if bot:
                actions = bot.actions(game, now)
                soft = False
            else:
                actions = repeater.feed(hub.poll(), now)
                soft = repeater.soft_dropping
            for action in actions:
                if action == T.QUIT:
                    stop.append(True)
                    break
                game.action(action, now)
            game.update(now, soft_dropping=soft)
            renderer.render(game, now)
            push()

            frames += 1
            if args.preview and frames % max(1, int(args.fps / 10)) == 0:
                sys.stdout.write("\x1b[H\x1b[2J" + canvas.to_ascii() + "\n")
                sys.stdout.flush()
            if args.stats and now - last_report >= 1.0:
                print("%.1f fps  score %d  lines %d  level %d"
                      % (frames / (now - last_report), game.score, game.lines,
                         game.level), file=sys.stderr)
                frames = 0
                last_report = now

            next_frame += frame
            delay = next_frame - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame = time.monotonic()  # fell behind, resynchronise
        return 0
    finally:
        try:
            canvas.clear()
            for _ in range(3):
                push()
        except OSError:
            pass
        if sender:
            sender.close()
        if hub:
            hub.close()


if __name__ == "__main__":
    sys.exit(main())
