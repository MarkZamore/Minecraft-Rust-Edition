"""Guard against accepting an empty PNG or unrelated magenta UI as rendered cards."""

import unittest

from PIL import Image, ImageDraw

from native_notices_qa import COLOR, measure_pixels, properties


class NativeNoticePixelProofTests(unittest.TestCase):
    def cards(self, count, dpi=1, width=None):
        image = Image.new("RGB", (1280, 720), (34, 80, 110))
        draw = ImageDraw.Draw(image)
        width = 240 * dpi if width is None else width
        for index in range(count):
            bottom = 720 - 12 * dpi - index * 90 * dpi
            draw.rectangle((12*dpi, bottom - 74*dpi, 12*dpi + width - 1, bottom - 1), fill=COLOR)
            draw.text((24*dpi, bottom - 60*dpi), "Visible notice", fill="black")
        return image

    def test_real_card_geometry_and_three_separate_cards(self):
        self.assertEqual(measure_pixels(self.cards(3), 3, 1)["count"], 3)
        one = measure_pixels(self.cards(1), 1, 1)["boxes"][0]
        two = measure_pixels(self.cards(1, 2), 1, 2)["boxes"][0]
        self.assertEqual(two[2] - two[0], 2 * (one[2] - one[0]))

    def test_empty_screenshot_cannot_pass_as_a_visible_notice(self):
        with self.assertRaisesRegex(AssertionError, "0 QA cards"):
            measure_pixels(self.cards(0), 1, 1)
        self.assertEqual(measure_pixels(self.cards(0), 0, 1)["count"], 0)

    def test_wrong_width_or_wrong_number_does_not_pass(self):
        with self.assertRaisesRegex(AssertionError, "width"):
            measure_pixels(self.cards(1, width=150), 1, 1)
        with self.assertRaisesRegex(AssertionError, "3 QA cards"):
            measure_pixels(self.cards(3), 1, 1)

    def test_utf8_properties_escape_newlines_and_separators(self):
        encoded = properties({"0.title": "Друзья = мир\nготов: да", "path": "a\\b"})
        self.assertIn("Друзья \\= мир\\nготов\\: да".encode("utf-8"), encoded)
        self.assertIn(b"path=a\\\\b\n", encoded)


if __name__ == "__main__":
    unittest.main()
