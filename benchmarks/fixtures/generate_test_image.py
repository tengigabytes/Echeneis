"""Generate a placeholder register map image for vision benchmarks.

Creates a simple table-style image with SX1276 register data.
Replace the output with a real datasheet screenshot for better testing.

Usage:
    python benchmarks/fixtures/generate_test_image.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _create_minimal_png(width: int, height: int, text_rows: list[str]) -> bytes:
    """Create a minimal valid PNG with text rendered as colored blocks.

    This creates a very basic image — enough for vision models to parse
    but not a pixel-perfect register map. For real benchmarks, replace
    with an actual datasheet screenshot.
    """
    # Create a simple white image with black text-like patterns
    # Each character becomes an 8x12 pixel block
    img_data = []
    char_h = 14
    char_w = 8
    actual_h = len(text_rows) * char_h + 20
    actual_w = max(len(r) for r in text_rows) * char_w + 20

    for y in range(actual_h):
        row_bytes = b"\x00"  # filter byte
        for x in range(actual_w):
            text_row_idx = (y - 10) // char_h
            text_col_idx = (x - 10) // char_w
            in_text = (
                0 <= text_row_idx < len(text_rows)
                and 0 <= text_col_idx < len(text_rows[text_row_idx])
                and text_rows[text_row_idx][text_col_idx] != " "
                and (y - 10) % char_h > 1
                and (y - 10) % char_h < char_h - 1
                and (x - 10) % char_w > 0
                and (x - 10) % char_w < char_w - 1
            )
            if in_text:
                row_bytes += b"\x20\x20\x20"  # dark gray
            else:
                row_bytes += b"\xff\xff\xff"  # white
        img_data.append(row_bytes)

    raw = b"".join(img_data)

    # Build PNG
    def chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", actual_w, actual_h, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def main() -> None:
    """Generate the test register map image."""
    rows = [
        "SX1276 Register Map (Selected)",
        "",
        "Address  Register         Default  Description",
        "-------  ---------------  -------  ---------------------------",
        "0x00     RegFifo          0x00     FIFO data input/output",
        "0x01     RegOpMode        0x09     Operating mode & LoRa/FSK",
        "0x06     RegFrMsb         0x6C     RF carrier freq MSB",
        "0x09     RegPaConfig      0x4F     PA selection & output power",
        "0x0B     RegOcp           0x2B     Over current protection",
        "0x0C     RegLna           0x20     LNA settings",
        "0x0D     RegFifoAddrPtr   0x00     FIFO SPI pointer",
        "0x1D     RegModemConfig1  0x72     BW, Coding Rate, Header",
        "0x1E     RegModemConfig2  0x70     SF, TX CRC, RX timeout",
    ]

    png_data = _create_minimal_png(600, 250, rows)
    out_path = Path(__file__).parent / "register_map.png"
    out_path.write_bytes(png_data)
    print(f"Generated {out_path} ({len(png_data)} bytes)")


if __name__ == "__main__":
    main()
