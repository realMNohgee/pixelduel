#!/usr/bin/env python3
"""pixelduel — Pixel-by-pixel image comparison. Zero dependencies, pure Python stdlib.

Compare two images pixel by pixel. Produces match statistics and optional
ASCII-art visual diffs. Works with PNG files using pure stdlib parsing.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

# ── PNG parser ──────────────────────────────────────────────────────────────

PNG_SIG = b"\x89PNG\r\n\x1a\n"


class PNGReadError(Exception):
    """Error reading or parsing a PNG file."""
    pass


def _read_chunk(f) -> tuple[bytes, bytes]:
    """Read one PNG chunk, returning (chunk_type, chunk_data)."""
    raw_len = f.read(4)
    if len(raw_len) < 4:
        return (b"", b"")
    length = struct.unpack(">I", raw_len)[0]
    chunk_type = f.read(4)
    if len(chunk_type) < 4:
        return (b"", b"")
    data = f.read(length) if length > 0 else b""
    f.read(4)  # CRC — skip
    return (chunk_type, data)


def _png_unfilter_row(row: bytes, prev_row: bytes | None, bpp: int) -> bytes:
    """Reverse PNG filter for a single row.

    bpp = bytes per pixel (e.g., 3 for RGB, 4 for RGBA).
    """
    filter_type = row[0]
    raw = bytearray(row[1:])
    if filter_type == 0:  # None
        pass
    elif filter_type == 1:  # Sub
        for i in range(bpp, len(raw)):
            raw[i] = (raw[i] + raw[i - bpp]) & 0xFF
    elif filter_type == 2:  # Up
        if prev_row is not None:
            for i in range(len(raw)):
                raw[i] = (raw[i] + prev_row[i + 1]) & 0xFF
    elif filter_type == 3:  # Average
        for i in range(len(raw)):
            left = raw[i - bpp] if i >= bpp else 0
            up = prev_row[i + 1] if prev_row is not None else 0
            raw[i] = (raw[i] + ((left + up) // 2)) & 0xFF
    elif filter_type == 4:  # Paeth
        for i in range(len(raw)):
            left = raw[i - bpp] if i >= bpp else 0
            up = prev_row[i + 1] if prev_row is not None else 0
            up_left = prev_row[i + 1 - bpp] if prev_row is not None and i >= bpp else 0
            p = left + up - up_left
            pa = abs(p - left)
            pb = abs(p - up)
            pc = abs(p - up_left)
            if pa <= pb and pa <= pc:
                pr = left
            elif pb <= pc:
                pr = up
            else:
                pr = up_left
            raw[i] = (raw[i] + pr) & 0xFF
    return bytes(raw)


def read_png(path: str) -> dict:
    """Read a PNG file and return pixel data + metadata.

    Returns dict with keys: width, height, color_type, pixels (list of
    (R,G,B) tuples row by row).
    """
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != PNG_SIG:
            raise PNGReadError(f"{path}: not a valid PNG file (bad signature)")

        # First chunk must be IHDR
        chunk_type, data = _read_chunk(f)
        if chunk_type != b"IHDR":
            raise PNGReadError(f"{path}: expected IHDR, got {chunk_type.decode(errors='replace')}")

        if len(data) < 13:
            raise PNGReadError(f"{path}: IHDR too short")

        width, height, bit_depth, color_type = struct.unpack(">IIBB", data[:10])

        if bit_depth != 8:
            raise PNGReadError(f"{path}: only 8-bit PNGs supported (got {bit_depth}-bit)")

        # Determine bytes per pixel
        if color_type == 2:  # RGB
            channels = 3
        elif color_type == 6:  # RGBA
            channels = 4
        elif color_type == 0:  # Grayscale
            channels = 1
        elif color_type == 4:  # Grayscale + Alpha
            channels = 2
        else:
            raise PNGReadError(f"{path}: unsupported color type {color_type} (only 0,2,4,6)")

        bpp = channels

        # Collect IDAT chunks
        idat_parts = []
        while True:
            chunk_type, data = _read_chunk(f)
            if chunk_type == b"":
                break
            if chunk_type == b"IDAT":
                idat_parts.append(data)
            elif chunk_type == b"IEND":
                break

        if not idat_parts:
            raise PNGReadError(f"{path}: no IDAT chunks found")

        # Decompress
        raw = zlib.decompress(b"".join(idat_parts))

        # Unfilter and extract pixels
        stride = width * bpp + 1  # +1 for filter byte
        pixels: list[tuple[int, int, int]] = []
        prev_row_raw = None

        for y in range(height):
            row_start = y * stride
            row_data = raw[row_start : row_start + stride]
            unfiltered = _png_unfilter_row(row_data, prev_row_raw, bpp)
            prev_row_raw = row_data

            for x in range(width):
                off = x * bpp
                if channels >= 3:
                    r = unfiltered[off]
                    g = unfiltered[off + 1]
                    b = unfiltered[off + 2]
                elif channels == 1:
                    g_val = unfiltered[off]
                    r = g = b = g_val
                else:  # Grayscale+Alpha
                    g_val = unfiltered[off]
                    r = g = b = g_val
                pixels.append((r, g, b))

    return {"width": width, "height": height, "color_type": color_type, "pixels": pixels}


def _parse_jpeg_dims(path: str) -> tuple[int, int] | None:
    """Parse JPEG dimensions from SOF markers. Returns (width, height) or None."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None
            while True:
                marker = f.read(2)
                if len(marker) < 2:
                    return None
                if marker[0] != 0xFF:
                    return None
                if marker[1] in (0xD8, 0x00, 0x01):
                    continue
                # SOF markers: 0xC0-0xC3, 0xC5-0xC7, 0xC9-0xCB, 0xCD-0xCF
                if 0xC0 <= marker[1] <= 0xC3 or 0xC5 <= marker[1] <= 0xC7 or \
                   0xC9 <= marker[1] <= 0xCB or 0xCD <= marker[1] <= 0xCF:
                    length = struct.unpack(">H", f.read(2))[0]
                    precision = struct.unpack(">B", f.read(1))[0]
                    height = struct.unpack(">H", f.read(2))[0]
                    width = struct.unpack(">H", f.read(2))[0]
                    return (width, height)
                else:
                    length = struct.unpack(">H", f.read(2))[0]
                    f.seek(length - 2, 1)
    except Exception:
        return None


def _parse_gif_dims(path: str) -> tuple[int, int] | None:
    """Parse GIF dimensions from header. Returns (width, height) or None."""
    try:
        with open(path, "rb") as f:
            header = f.read(6)
            if header not in (b"GIF89a", b"GIF87a"):
                return None
            width, height = struct.unpack("<HH", f.read(4))
            return (width, height)
    except Exception:
        return None


def load_image(path: str):
    """Try to load image, preferring PNG. Returns dict with width, height, pixels."""
    p = Path(path)
    if not p.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    ext = p.suffix.lower()

    # Try PNG first
    try:
        return read_png(path)
    except PNGReadError as e:
        pass
    except Exception:
        pass

    # For non-PNG, try to get dimensions but can't do pixel comparison
    dims = None
    if ext in (".jpg", ".jpeg"):
        dims = _parse_jpeg_dims(path)
    elif ext == ".gif":
        dims = _parse_gif_dims(path)

    if dims:
        print(f"Error: {path} appears to be a {ext.upper()} image. "
              f"pixelduel supports full pixel comparison for PNG only. "
              f"Dimensions detected: {dims[0]}x{dims[1]}", file=sys.stderr)
        sys.exit(2)
    else:
        print(f"Error: could not read {path} as a supported image format", file=sys.stderr)
        sys.exit(2)


# ── Comparison logic ────────────────────────────────────────────────────────

def compare_pixels(img1: dict, img2: dict, threshold: int = 10) -> dict:
    """Compare two loaded images pixel by pixel.

    Returns a dict with comparison statistics.
    """
    w1, h1 = img1["width"], img1["height"]
    w2, h2 = img2["width"], img2["height"]

    if w1 != w2 or h1 != h2:
        print(f"Error: image dimensions differ: {w1}x{h1} vs {w2}x{h2}", file=sys.stderr)
        sys.exit(1)

    total = w1 * h1
    pixels1 = img1["pixels"]
    pixels2 = img2["pixels"]
    diff_count = 0
    diff_mask: list[bool] = []

    for i in range(total):
        r1, g1, b1 = pixels1[i]
        r2, g2, b2 = pixels2[i]
        if (abs(r1 - r2) >= threshold or
                abs(g1 - g2) >= threshold or
                abs(b1 - b2) >= threshold):
            diff_count += 1
            diff_mask.append(True)
        else:
            diff_mask.append(False)

    match_count = total - diff_count
    match_pct = (match_count / total) * 100 if total > 0 else 0
    diff_pct = (diff_count / total) * 100 if total > 0 else 0

    return {
        "total_pixels": total,
        "matching_pixels": match_count,
        "diff_pixels": diff_count,
        "match_percent": round(match_pct, 2),
        "diff_percent": round(diff_pct, 2),
        "dimensions": {"width": w1, "height": h1},
        "diff_mask": diff_mask,
        "threshold": threshold,
    }


def ascii_diff(stats: dict, block_width: int = 80) -> str:
    """Generate an ASCII-art visual diff from comparison results."""
    mask = stats["diff_mask"]
    w = stats["dimensions"]["width"]
    h = stats["dimensions"]["height"]

    # Scale to fit terminal width; each block represents block_w x block_h pixels
    ratio = max(1, w // block_width)
    bw = w // ratio
    bh = h // ratio if ratio > 0 else h

    lines = []
    for row in range(bh):
        line_chars = []
        for col in range(bw):
            # Check if any pixel in this block differs
            block_has_diff = False
            for dy in range(ratio):
                for dx in range(ratio):
                    py = row * ratio + dy
                    px = col * ratio + dx
                    if py < h and px < w:
                        idx = py * w + px
                        if idx < len(mask) and mask[idx]:
                            block_has_diff = True
                            break
                if block_has_diff:
                    break
            line_chars.append("█" if block_has_diff else "░")
        lines.append("".join(line_chars))
    return "\n".join(lines)


# ── Subcommand handlers ─────────────────────────────────────────────────────

def cmd_diff(args: argparse.Namespace) -> int:
    """Pixel-by-pixel comparison producing statistics."""
    img1 = load_image(args.img1)
    img2 = load_image(args.img2)
    stats = compare_pixels(img1, img2, threshold=args.threshold)

    if args.format == "json":
        out = {k: v for k, v in stats.items() if k != "diff_mask"}
        print(json.dumps(out, indent=2))
    else:
        print(f"Image 1: {args.img1}")
        print(f"Image 2: {args.img2}")
        print(f"Dimensions: {stats['dimensions']['width']}x{stats['dimensions']['height']}")
        print(f"Threshold: {stats['threshold']} (min RGB channel difference)")
        print(f"Total pixels:  {stats['total_pixels']:,}")
        print(f"Matching:      {stats['matching_pixels']:,} ({stats['match_percent']}%)")
        print(f"Different:     {stats['diff_pixels']:,} ({stats['diff_percent']}%)")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Pixel-by-pixel comparison with ASCII-art visual diff."""
    img1 = load_image(args.img1)
    img2 = load_image(args.img2)
    stats = compare_pixels(img1, img2, threshold=args.threshold)

    if args.format == "json":
        out = {k: v for k, v in stats.items() if k != "diff_mask"}
        print(json.dumps(out, indent=2))
    else:
        print(f"Image 1: {args.img1}")
        print(f"Image 2: {args.img2}")
        print(f"Dimensions: {stats['dimensions']['width']}x{stats['dimensions']['height']}")
        print(f"Threshold: {stats['threshold']} (min RGB channel difference)")
        print(f"Total pixels:  {stats['total_pixels']:,}")
        print(f"Matching:      {stats['matching_pixels']:,} ({stats['match_percent']}%)")
        print(f"Different:     {stats['diff_pixels']:,} ({stats['diff_percent']}%)")
        print()
        print("Visual diff (█ = different, ░ = matching):")
        print(ascii_diff(stats))
    return 0


# ── Parser ───────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pixelduel",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    common.add_argument("--threshold", type=int, default=10,
                        help="Min difference in any RGB channel to count as different (default: 10)")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("diff", parents=[common], help="Pixel-by-pixel comparison producing statistics")
    s.add_argument("img1", help="Path to first image (PNG preferred)")
    s.add_argument("img2", help="Path to second image (PNG preferred)")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("compare", parents=[common], help="Same as diff + ASCII visual diff")
    s.add_argument("img1", help="Path to first image (PNG preferred)")
    s.add_argument("img2", help="Path to second image (PNG preferred)")
    s.set_defaults(func=cmd_compare)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
