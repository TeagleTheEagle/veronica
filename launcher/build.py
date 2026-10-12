"""Builds VERONICA.exe in the veronica folder: draws the icon (launcher/veronica.ico) and compiles launcher/VERONICA.cs
with the C# compiler that comes with Windows (.NET Framework 4). Nothing is downloaded or installed.

    .venv\\Scripts\\python.exe launcher\\build.py
"""
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
FOLDER = HERE.parent
ICON = HERE / 'veronica.ico'
EXE = FOLDER / 'VERONICA.exe'
CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
SIZES = (256, 64, 48, 32, 16)
NAVY, TEAL = np.array([11, 18, 32]), np.array([34, 211, 238])


def _segment_distance(x, y, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = np.clip(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy), 0, 1)
    return np.hypot(x - (ax + t * dx), y - (ay + t * dy))


def draw(size, sample=4):
    """RGBA pixels of the icon: a navy disc with a teal ring and a teal V."""
    n = size * sample
    y, x = (np.mgrid[0:n, 0:n] + 0.5) / n
    r = np.hypot(x - 0.5, y - 0.5)
    disc = r <= 0.48
    ring = disc & (r >= 0.43)
    v = np.minimum(_segment_distance(x, y, (0.30, 0.30), (0.50, 0.72)),
                   _segment_distance(x, y, (0.70, 0.30), (0.50, 0.72))) <= 0.065
    teal = (ring | (v & disc)).astype(float)
    alpha = disc.astype(float)
    shade = 1.0 - 0.35 * np.clip((y - 0.2), 0, 1)  # a little darker towards the bottom
    rgb = (NAVY[None, None, :] * shade[..., None]) * (1 - teal[..., None]) + TEAL[None, None, :] * teal[..., None]
    rgba = np.concatenate([rgb, alpha[..., None] * 255], axis=-1)
    rgba = rgba.reshape(size, sample, size, sample, 4).mean(axis=(1, 3))  # smooth edges
    return np.clip(rgba.round(), 0, 255).astype(np.uint8)


def png(pixels):
    height, width, _ = pixels.shape
    raw = b''.join(b'\x00' + pixels[row].tobytes() for row in range(height))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def ico(images):
    """An .ico holding PNG images (Windows Vista and later read these)."""
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b'', b''
    for size, image in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(image), offset + len(data))
        data += image
    return header + entries + data


def main():
    if not CSC.is_file():
        sys.exit(f'The C# compiler that comes with Windows was not found at {CSC}.')
    ICON.write_bytes(ico([(size, png(draw(size))) for size in SIZES]))
    result = subprocess.run([str(CSC), '/nologo', '/target:winexe', '/optimize+', '/reference:System.Windows.Forms.dll',
                             '/reference:System.Core.dll', f'/win32icon:{ICON}', f'/out:{EXE}',
                             str(HERE / 'VERONICA.cs')], capture_output=True, text=True)
    if result.returncode:
        sys.exit(result.stdout + result.stderr)
    print(f'Built {EXE} ({EXE.stat().st_size // 1024} KB) with the icon {ICON.name}.')


if __name__ == '__main__':
    main()
