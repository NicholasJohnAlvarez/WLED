# Real-time Tetris on a WLED matrix, driven from a Raspberry Pi

A playable Tetris that runs on a Raspberry Pi and streams finished frames to an
existing WLED controller over DDP. Nothing on the ESP32 changes: WLED keeps
owning the LED timing, power limiting and panel mapping, and the Pi just
becomes another realtime source — the same path a Hyperion or xLights setup
uses.

Built for a 48x32 matrix (six 16x16 WS2812B panels, 2 rows x 3 columns, 1536
pixels, three output channels of 512) on an ESP32-S3, but the geometry is all
command-line options.

```
  USB gamepad ──► Raspberry Pi ──── UDP/DDP :4048 ────► ESP32-S3 (WLED) ──► 3x512 WS2812B
                  game + render      4 packets/frame     ledmap + output
                  60 fps             4608 bytes/frame    ~60 fps ceiling
```

## Why this works without touching the firmware

* WLED listens for DDP on UDP port 4048 unconditionally — there is no setting
  to switch on (`wled00/wled.cpp`, `ddp.begin(false, DDP_DEFAULT_PORT)`).
* Incoming DDP pixels go through `setRealtimePixel()` →
  `WS2812FX::setRealtimePixelColor()` → `setPixelColor(i)`, which writes the
  **logical** pixel buffer. The 2D panel ledmap is applied later, at show time,
  in `getMappedPixelIndex()` (`wled00/FX.h`).
* So DDP pixel index `y * matrix_width + x` is the whole contract. Serpentine
  wiring, panel order, and the split across the three output channels are
  already handled by your WLED matrix configuration, and this tool never needs
  to know about them.

That mapping is gated on **Respect LED Maps** (`realtimeRespectLedMaps`, on by
default). If you would rather do the mapping on the Pi, turn it off in WLED and
pass `--ledmap your-ledmap.json` instead.

## Which Pi

Any of them will run the game; the differences are network quality and headroom.

| Board | Verdict |
| --- | --- |
| **Pi 4B / 3B+ (wired Ethernet)** | Best choice. Ethernet removes one of the two wireless hops, which is where nearly all the jitter lives. |
| **Pi Zero 2 W** | Good if you want it tucked behind the panel. Quad-core, plenty fast, but Wi-Fi only. |
| **Pi Zero W (original)** | Works. ARMv6 at 1 GHz is ~20x slower than a desktop core for Python, which still leaves the render well inside a frame — run `--fps 40` and use `--stats` to confirm. |

Render + game logic measures **0.24 ms/frame** on an x86 core for the 48x32
layout, so even the original Zero W has room at 60 fps. The limit is the
network, not the CPU.

Your ESP32-S3 has no Ethernet option — WLED's wired-network support uses the
classic ESP32's RMII MAC (see `WLED_ETH_*` in `wled00/const.h`), which the S3
does not have. The S3 stays on 2.4 GHz Wi-Fi either way, so the thing worth
optimising is the Pi's hop.

## Numbers

* **Frame size** — 1536 px x 3 bytes = 4608 bytes, sent as 4 DDP packets
  (1440 + 1440 + 1440 + 288). Only the last carries the PUSH flag, so WLED
  renders a whole frame at once instead of tearing.
* **Bandwidth at 60 fps** — 240 packets/s, ~276 kB/s (~2.2 Mbit/s payload).
  Nothing for Wi-Fi; the packet rate matters more than the bit rate.
* **Output ceiling** — WS2812B is 30 µs per LED. 512 LEDs per channel is
  15.4 ms plus reset, and the three channels run in parallel on the S3, so the
  strip itself tops out near **63 fps**. Asking for more than 60 just drops
  frames.
* **Latency, button to light** — ~1-8 ms USB poll, 0-17 ms waiting for the next
  game tick, a few ms of render, 2-10 ms over Wi-Fi (with occasional spikes on
  a busy 2.4 GHz band), plus one ~16 ms strip refresh. **Call it 30-50 ms, two
  or three frames.** Imperceptible for Tetris; you would notice it in a
  rhythm game.

## Install

Nothing to install — Python 3.7+ from the standard library only, no numpy, no
`evdev` package. Copy the directory to the Pi:

```sh
scp -r tools/pi_tetris pi@raspberrypi.local:~/
```

Give yourself access to the controller without `sudo`:

```sh
sudo usermod -aG input "$USER"   # log out and back in
```

## First light

Check orientation before you play. This draws corner markers and axis ramps:

```sh
./run.py --host 192.168.1.50 --panel 48x32 --test-pattern
```

Red is top-left, green top-right, blue bottom-left, white bottom-right, and
"TOP" is written near the top-left corner. If it comes out sideways or
mirrored, add `--rotate 90|180|270`, `--flip-x` or `--flip-y` until it matches
the panel in front of you, then keep those flags for every later run.

Then let the bot play it, which also doubles as a throughput test:

```sh
./run.py --host 192.168.1.50 --panel 48x32 --demo --stats
```

And play:

```sh
./run.py --host 192.168.1.50 --panel 48x32
```

No hardware to hand? `--dry-run --preview` renders to the terminal instead.

## Layout

The HUD lays itself out to fit whatever canvas it is given, so the same command
works in either mounting.

**48x32 landscape** — a 10x20 playfield at 1 px per cell on the left, with the
next-piece queue, hold slot, score, lines and level in the 35-column HUD beside
it.

**32x48 portrait** (`--rotate 90`) — a 10x20 playfield at **2 px per cell**,
previews and hold in the side column, and score/lines/level in a strip across
the bottom (white, cyan and amber respectively).

`--cols` and `--rows` change the playfield; the renderer picks the largest cell
size that still leaves room for a HUD, and warns if the playfield cannot fit at
all.

## Controls

Read straight from `/dev/input/event*`, so USB gamepads and USB or Bluetooth
keyboards both work, with real press/release timing (and therefore proper DAS
and ARR — 170 ms then 40 ms).

| Action | Keyboard | Gamepad |
| --- | --- | --- |
| Move | ← → | D-pad / left stick |
| Soft drop | ↓ | D-pad down |
| Hard drop | Space, ↑ | Right shoulder, D-pad up |
| Rotate CW / CCW | X / Z | A / B |
| Rotate 180 | A | X |
| Hold | C, Shift | Y, left shoulder |
| Pause | P, Esc | Start |
| Restart | R | Select |
| Quit | Q | — |

Odd controller? Record your own:

```sh
./run.py --list-devices
./run.py --map-controller          # saves ~/.config/wled-tetris/controls.json
```

`--keyboard` additionally reads the terminal you launched from, which is handy
over SSH — but a terminal gives no key-release events, so you get taps instead
of held repeats. Prefer evdev for actual play. `--grab` claims the input
devices exclusively so your keystrokes do not also land in the shell.

## WLED settings

Config → Sync Interfaces:

| Setting | Value | Why |
| --- | --- | --- |
| Respect LED Maps | **on** (default) | makes `y * width + x` addressing work |
| Use main segment only | **off** (default) | otherwise only one segment is fed |
| Realtime timeout | 2500 ms (default) | how long after the last frame WLED returns to its own effects |
| Force max brightness | your call | on = ignore the brightness slider for realtime data; off = the slider still scales the game |
| Skip out-of-sequence packets | optional | safe either way: one sequence number per frame, 1-15, never 0 |

Config → WiFi Setup: **disable Wi-Fi sleep** (already the ESP32 default here)
and give the controller a static lease. Keep the AP on a quiet 2.4 GHz channel.

Config → LED Preferences: leave the **automatic brightness limiter** configured
for your supply. 1536 WS2812B at full white is theoretically ~90 A, and the ABL
is what keeps a bright Tetris field from browning out your rails. `--brightness
0.5` on the Pi side trims it before the data ever leaves, which also halves
what the limiter has to claw back.

## Run it as a service

```sh
sudo cp wled-tetris.service /etc/systemd/system/
sudoedit /etc/systemd/system/wled-tetris.service   # set the IP, user and flags
sudo systemctl enable --now wled-tetris
```

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Nothing lights up | Wrong IP, or something else is holding realtime. Check `http://<wled>/json/state` for `"live": true`. |
| Panel shows the game scrambled across panels | WLED's 2D matrix setup does not match the wiring, or Respect LED Maps is off. Fix it with the test pattern and the 2D config, not here. |
| Sideways or mirrored | `--rotate` / `--flip-x` / `--flip-y`. |
| Only part of the grid updates | "Use main segment only" is on, or a packet is being dropped — check for Wi-Fi interference. |
| Flashes back to an effect | The realtime timeout expired; the Pi stopped sending. |
| Stutters | Try `--fps 40`, move the Pi to Ethernet, or check `--stats` for the real frame rate. |
| No controller found | `--list-devices`; add yourself to the `input` group. |

## Alternative: no Pi at all

Tetris is small enough to run on the ESP32-S3 itself as a WLED usermod, which
removes the network from the loop entirely (latency drops to a single frame).
The reasons to use a Pi anyway, and why this exists:

* USB gamepads just work, instead of wiring buttons to GPIOs.
* Python iterates in seconds, with no reflash.
* The S3's RAM and CPU stay free for WLED.
* The same DDP path will carry anything else you want to put on the panel.

## Files

| File | |
| --- | --- |
| `run.py` | CLI and main loop |
| `ddp.py` | DDP framing and sending |
| `canvas.py` | framebuffer, rotation/flip, ledmap, ASCII preview |
| `renderer.py` | playfield and self-fitting HUD |
| `tetris.py` | game rules: SRS + wall kicks, 7-bag, hold, lock delay, scoring |
| `controls.py` | evdev and terminal input, DAS/ARR, controller mapping |
| `bot.py` | heuristic player for `--demo` |
| `test_pi_tetris.py` | tests, including WLED's DDP parser reimplemented to check the framing |

```sh
python3 test_pi_tetris.py
```
