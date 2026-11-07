"""Semantic raster regression checks for the official Rust window and PE icons."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import unittest

from PIL import Image


FOLDER = Path(__file__).resolve().parents[1] / "integration/steam/rust-logo"
SPEC = importlib.util.spec_from_file_location("edition_rust_artwork", FOLDER / "render.py")
artwork = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artwork)


class RustArtworkTests(unittest.TestCase):
    def test_pinned_png_reproduces_the_original_svg_geometry(self):
        rendered = artwork.render_frame((FOLDER / "rust-logo.svg").read_bytes(), 256)
        self.assertEqual(rendered, (FOLDER / "rust-logo.png").read_bytes())
        lock = json.loads((FOLDER / "artwork-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(rendered).hexdigest(), lock["files"]["rust-logo.png"]["sha256"])

    def test_letter_ring_and_all_five_mount_holes_are_visible(self):
        image = Image.open(FOLDER / "rust-logo.png").convert("RGBA")
        at = lambda x, y: image.getpixel((round(256 * (0.06 + 0.88 * x / 106)),
                                          round(256 * (0.06 + 0.88 * y / 106))))
        # Source geometry is unchanged, scaled into the backplate's six-percent inset.
        self.assertLess(max(at(33, 48)[:3]), 16)  # R stem, not merely an empty gear.
        self.assertGreater(min(at(49, 42)[:3]), 240)  # R counter shows the white backplate.
        self.assertLess(max(at(53, 7)[:3]), 16)  # Gear ring.
        for x, y in [(53, 13), (91.04, 40.64), (76.51, 85.36),
                     (29.49, 85.36), (14.96, 40.64)]:
            with self.subTest(hole=(x, y)):
                self.assertGreater(min(at(x, y)[:3]), 240)
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((255, 255))[3], 0)

    def test_all_seven_pe_frames_have_the_same_transparent_artwork(self):
        ico = (FOLDER / "rust-edition.ico").read_bytes()
        self.assertEqual(struct.unpack_from("<HHH", ico), (0, 1, 7))
        for index, expected_size in enumerate(artwork.SIZES):
            width, height, colors, reserved, planes, bits, length, offset = struct.unpack_from(
                "<BBBBHHII", ico, 6 + 16 * index)
            with self.subTest(size=expected_size):
                self.assertEqual((width or 256, height or 256), (expected_size, expected_size))
                self.assertEqual((colors, reserved, planes, bits), (0, 0, 1, 32))
                self.assertLessEqual(offset + length, len(ico))
                png = ico[offset:offset + length]
                image = Image.open(io.BytesIO(png))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.size, (expected_size, expected_size))
                artwork.validate_frame(image)
                self.assertEqual(png, artwork.render_frame((FOLDER / "rust-logo.svg").read_bytes(), expected_size))

    def test_previous_white_mask_rectangle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "transparent background"):
            artwork.validate_frame(Image.new("RGBA", (256, 256), "white"))

    def test_smallest_window_frame_has_visible_artwork_on_both_caption_themes(self):
        frame = Image.open(io.BytesIO(artwork.render_frame((FOLDER / "rust-logo.svg").read_bytes(), 16)))
        for background in ("black", "white"):
            with self.subTest(background=background):
                composite = Image.new("RGBA", (16, 16), background)
                composite.alpha_composite(frame)
                luminance = list(composite.convert("L").get_flattened_data())
                self.assertGreater(sum(value > 200 for value in luminance), 32)
                self.assertGreater(sum(value < 64 for value in luminance), 32)

    def test_monochrome_dark_caption_regression_is_rejected(self):
        frame = Image.open(FOLDER / "rust-logo.png").convert("RGBA")
        black = Image.new("RGBA", frame.size, "black")
        black.putalpha(frame.getchannel("A"))
        with self.assertRaisesRegex(ValueError, "contrast"):
            artwork.validate_frame(black)

    def test_missing_r_cannot_pass_as_an_intact_gear(self):
        svg = (FOLDER / "rust-logo.svg").read_bytes()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(svg)
        letter = next(node for node in root.iter() if node.get("id") == "r")
        letter.set("d", "")
        with self.assertRaisesRegex(ValueError, "silhouette|missing"):
            artwork.render_frame(ET.tostring(root), 256)


if __name__ == "__main__":
    unittest.main()
