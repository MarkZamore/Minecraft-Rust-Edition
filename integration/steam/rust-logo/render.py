#!/usr/bin/env python3
"""Reproduce the pinned Rust programming language icon rasterizations."""

import argparse
import hashlib
import json
import struct
from pathlib import Path

import cairocffi
import cairosvg

HERE = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    lock = json.loads((HERE / "artwork-lock.json").read_text(encoding="utf-8"))
    if lock["rasterizer"] != {
        "CairoSVG": cairosvg.__version__,
        "Cairo": cairocffi.cairo_version_string(),
    }:
        parser.error("Use the exact pinned CairoSVG and Cairo versions")
    svg = (HERE / "rust-logo.svg").read_bytes()
    blob = f"blob {len(svg)}\0".encode("ascii") + svg
    if hashlib.sha1(blob).hexdigest() != lock["gitBlob"]:
        parser.error("The source SVG does not match its pinned Git blob")
    frames = [cairosvg.svg2png(bytestring=svg, output_width=size, output_height=size)
              for size in SIZES]
    offset = 6 + 16 * len(SIZES)
    entries = []
    for size, png in zip(SIZES, frames, strict=True):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset))
        offset += len(png)
    ico = struct.pack("<HHH", 0, 1, len(SIZES)) + b"".join(entries) + b"".join(frames)
    for name, data in {"rust-logo.svg": svg, "rust-logo.png": frames[-1], "rust-edition.ico": ico}.items():
        pin = lock["files"][name]
        if pin != {"sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data)}:
            parser.error(f"{name} did not reproduce its pinned output")
        path = HERE / name
        if args.write and name != "rust-logo.svg":
            path.write_bytes(data)
        elif path.read_bytes() != data:
            parser.error(f"{name} on disk differs from the pinned rendering")


if __name__ == "__main__":
    main()
