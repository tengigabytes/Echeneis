"""Generate a readable register map image for vision benchmarks.

Creates a clean table-style image with SX1276 register data using Pillow.

Usage:
    python benchmarks/fixtures/generate_test_image.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_REGISTERS = [
    ("0x00", "RegFifo", "0x00", "FIFO data input/output"),
    ("0x01", "RegOpMode", "0x09", "Operating mode & LoRa/FSK"),
    ("0x06", "RegFrMsb", "0x6C", "RF carrier freq MSB"),
    ("0x09", "RegPaConfig", "0x4F", "PA selection & output power"),
    ("0x0B", "RegOcp", "0x2B", "Over current protection"),
    ("0x0C", "RegLna", "0x20", "LNA settings"),
    ("0x0D", "RegFifoAddrPtr", "0x00", "FIFO SPI pointer"),
    ("0x1D", "RegModemConfig1", "0x72", "BW, Coding Rate, Header"),
    ("0x1E", "RegModemConfig2", "0x70", "SF, TX CRC, RX timeout"),
]

_HEADER = ("Address", "Register", "Default", "Description")

# Column widths in pixels
_COL_W = [80, 160, 80, 280]
_ROW_H = 28
_PAD = 12
_MARGIN = 20


def main() -> None:
    """Generate the test register map image."""
    font = ImageFont.load_default(size=16)
    title_font = ImageFont.load_default(size=20)

    n_rows = len(_REGISTERS) + 1  # +1 for header
    width = sum(_COL_W) + _MARGIN * 2
    height = _ROW_H * n_rows + _MARGIN * 2 + 40  # +40 for title

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Title
    draw.text(
        (_MARGIN, _MARGIN),
        "SX1276 Register Map (Selected Registers)",
        fill="black",
        font=title_font,
    )

    y_start = _MARGIN + 36

    # Draw header
    x = _MARGIN
    for i, hdr in enumerate(_HEADER):
        draw.text((x + _PAD, y_start + 4), hdr, fill="black", font=font)
        x += _COL_W[i]

    # Header underline
    draw.line(
        [(_MARGIN, y_start + _ROW_H), (width - _MARGIN, y_start + _ROW_H)],
        fill="black",
        width=2,
    )

    # Draw rows
    for row_idx, reg in enumerate(_REGISTERS):
        y = y_start + _ROW_H * (row_idx + 1)
        x = _MARGIN
        for col_idx, val in enumerate(reg):
            draw.text((x + _PAD, y + 4), val, fill="black", font=font)
            x += _COL_W[col_idx]

        # Alternating row background for readability
        if row_idx % 2 == 0:
            y_top = y
            draw.rectangle(
                [(_MARGIN, y_top), (width - _MARGIN, y_top + _ROW_H)],
                fill="#f0f0f0",
                outline=None,
            )
            # Redraw text over background
            x = _MARGIN
            for col_idx, val in enumerate(reg):
                draw.text((x + _PAD, y + 4), val, fill="black", font=font)
                x += _COL_W[col_idx]

    out_path = Path(__file__).parent / "register_map.png"
    img.save(out_path)
    print(f"Generated {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
