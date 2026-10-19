"""Minimal DDP (Distributed Display Protocol) sender for WLED.

WLED listens for DDP on UDP port 4048 at all times - there is no setting to
enable it (see wled00/wled.cpp, `ddp.begin(false, DDP_DEFAULT_PORT)`).

Frames larger than one datagram are split; only the final packet carries the
PUSH flag, so WLED renders the complete frame in one go instead of tearing.
"""

import socket
import struct

DDP_PORT = 4048

_HEADER_LEN = 10
# WLED's receive buffer (e131_packet_t.raw) is 1458 bytes, so 1448 bytes of
# payload would fit. Stick to 1440 (480 RGB pixels) to match what WLED itself
# sends and to stay clear of any MTU rounding.
MAX_DATA_LEN = 1440

_FLAGS_VER1 = 0x40
_FLAGS_PUSH = 0x01
_TYPE_RGB24 = 0x0B  # 0b00_001_011 -> 3 channels, 8 bit each
_ID_DISPLAY = 1


class DDPSender:
    """Sends RGB frames to a WLED device over DDP."""

    def __init__(self, host, port=DDP_PORT, max_data_len=MAX_DATA_LEN):
        self.host = host
        self.port = port
        self.max_data_len = max_data_len - (max_data_len % 3)
        self._addr = (host, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 16)
        self._seq = 0
        self.packets_sent = 0

    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def send(self, data):
        """Send one frame. `data` is a bytes-like of R,G,B triples."""
        total = len(data)
        # One sequence number per frame. WLED's optional "skip out of sequence"
        # filter compares against the last pushed sequence, and sharing the
        # number across a frame's packets keeps that comparison happy.
        self._seq = self._seq % 15 + 1
        offset = 0
        while True:
            chunk = min(self.max_data_len, total - offset)
            last = offset + chunk >= total
            flags = _FLAGS_VER1 | (_FLAGS_PUSH if last else 0)
            header = struct.pack(
                ">BBBBIH", flags, self._seq, _TYPE_RGB24, _ID_DISPLAY, offset, chunk
            )
            self._sock.sendto(header + bytes(data[offset:offset + chunk]), self._addr)
            self.packets_sent += 1
            offset += chunk
            if last:
                break

    def send_black(self, pixel_count):
        self.send(bytes(pixel_count * 3))
