#!/usr/bin/env python3
"""Reproduce the pinned Rust programming language icon rasterizations."""

import argparse
import copy
import hashlib
import io
import json
import struct
from pathlib import Path
import xml.etree.ElementTree as ET

import cairocffi
import cairosvg
from PIL import Image, ImageChops, __version__ as pillow_version

HERE = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 128, 256)
SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


def render_frame(svg, size):
    """Apply the original SVG's luminance mask, which CairoSVG does not support.

    The upstream SVG keeps the mask below the painted paths. Rendering it whole
    with CairoSVG paints that mask as a white rectangle over the logo. Render the
    original R and gear separately and multiply the gear's alpha by the original
    white/black mask, preserving the source geometry and paint order.
    """
    root = ET.fromstring(svg)
    logo = root.find(f"{{{SVG}}}g")
    if logo is None or [node.get("id") for node in logo] != ["r", "gear", "holes"]:
        raise ValueError("Unexpected pinned Rust SVG structure")
    r, gear, mask = logo
    if gear.get("mask") != "url(#holes)" or mask.tag != f"{{{SVG}}}mask":
        raise ValueError("The pinned Rust gear luminance mask changed")

    def raster(nodes):
        layer = copy.deepcopy(root)
        group = layer.find(f"{{{SVG}}}g")
        for node in list(group):
            group.remove(node)
        group.extend(copy.deepcopy(nodes))
        png = cairosvg.svg2png(bytestring=ET.tostring(layer),
                              output_width=size, output_height=size)
        return Image.open(io.BytesIO(png)).convert("RGBA")

    gear = copy.deepcopy(gear)
    del gear.attrib["mask"]
    r_image = raster([r])
    gear_image = raster([gear])
    luminance = raster(list(mask)).convert("L")
    gear_image.putalpha(ImageChops.multiply(gear_image.getchannel("A"), luminance))
    frame = Image.alpha_composite(r_image, gear_image)
    validate_frame(frame)
    result = io.BytesIO()
    frame.save(result, format="PNG", compress_level=9)
    return result.getvalue()


def validate_frame(frame):
    """Reject opaque mask rectangles or missing main artwork before pinning it."""
    if frame.mode != "RGBA" or frame.width != frame.height:
        raise ValueError("The Rust icon must be a square RGBA image")
    alpha = frame.getchannel("A")
    size = frame.width
    if any(alpha.getpixel(point) for point in [(0, 0), (0, size - 1),
                                               (size - 1, 0), (size - 1, size - 1)]):
        raise ValueError("The Rust icon lost its transparent background")
    coverage = sum(value * count for value, count in enumerate(alpha.histogram())) / (255 * size * size)
    if not 0.25 < coverage < 0.65:
        raise ValueError("The Rust icon is missing its R/gear or has a painted mask")
    # Interior of the R's stem and its upper counter in the original 106-unit SVG.
    if size >= 64:
        point = lambda x, y: (round(size * x / 106), round(size * y / 106))
        if alpha.getpixel(point(33, 48)) < 240 or alpha.getpixel(point(49, 42)) > 16:
            raise ValueError("The Rust letter silhouette or its counter is missing")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    lock = json.loads((HERE / "artwork-lock.json").read_text(encoding="utf-8"))
    if lock["rasterizer"] != {
        "CairoSVG": cairosvg.__version__,
        "Cairo": cairocffi.cairo_version_string(),
        "Pillow": pillow_version,
        "mask": "explicit-luminance-composition-v1",
    }:
        parser.error("Use the exact pinned CairoSVG and Cairo versions")
    svg = (HERE / "rust-logo.svg").read_bytes()
    blob = f"blob {len(svg)}\0".encode("ascii") + svg
    if hashlib.sha1(blob).hexdigest() != lock["gitBlob"]:
        parser.error("The source SVG does not match its pinned Git blob")
    frames = [render_frame(svg, size) for size in SIZES]
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
