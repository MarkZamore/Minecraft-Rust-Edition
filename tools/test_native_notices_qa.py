"""Guard against accepting an empty PNG or unrelated magenta UI as rendered cards."""

from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image, ImageDraw

from native_notices_qa import (COLOR, atomic_write, check_mods, measure_pixels,
                               properties, remove_fixture, require_main_title, snapshot)


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


class NativeNoticeHarnessTests(unittest.TestCase):
    def option_session(self, screen, final):
        class Session:
            def __init__(self):
                self.index = 0
                self.clicked = []
                self.pages = [
                    {"screen": screen, "widgets": [
                        {"id": name, "enabled": True}
                        for name in ["browse-mods", "open-mod-folder", "done"]]},
                    {"screen": screen, "widgets": [{"id": "done", "enabled": True}]},
                    final,
                ]

            def control(self, *words):
                if words == ("options", "mods"):
                    self.index = 0
                elif words == ("ui",):
                    return self.pages[self.index]
                elif words == ("click", "done"):
                    self.clicked.append("done")
                    self.index += 1
                else:
                    raise AssertionError(words)

        return Session()

    def test_title_mods_pages_close_even_when_screen_label_stays_title(self):
        final = {"screen": "title", "widgets": [
            {"id": "singleplayer", "label": "Singleplayer", "enabled": True},
            {"id": "multiplayer", "label": "Multiplayer", "enabled": True}]}
        session = self.option_session("title", final)
        mods, returned = check_mods(session)
        self.assertEqual(mods, ["browse-mods", "done", "open-mod-folder"])
        self.assertEqual(session.clicked, ["done", "done"])
        require_main_title(returned)

    def test_world_mods_pages_close_back_to_original_pause(self):
        final = {"screen": "paused", "widgets": [{"id": "return", "enabled": True}]}
        session = self.option_session("options", final)
        _, returned = check_mods(session)
        self.assertEqual(session.clicked, ["done", "done"])
        self.assertEqual(returned, final)

    def test_mods_widgets_cannot_be_title_proof(self):
        with self.assertRaisesRegex(AssertionError, "actual main title"):
            require_main_title({"screen": "title", "widgets": [
                {"id": "browse-mods", "enabled": True}, {"id": "done", "enabled": True}]})

    def test_atomic_retry_reuses_original_bytes_and_notice_timestamp(self):
        path, temporary = Mock(spec=Path), Mock(spec=Path)
        path.name = "notices.properties"
        path.with_name.return_value = temporary
        data, created = snapshot(1, created=1234567)
        with patch("native_notices_qa.os.replace", side_effect=[PermissionError("reader lock"), None]) as replace, \
                patch("native_notices_qa.time.monotonic", return_value=0), \
                patch("native_notices_qa.time.sleep"):
            atomic_write(path, data)
        temporary.write_bytes.assert_called_once_with(data)
        self.assertEqual(created, 1234567)
        self.assertIn(b"0.createdUnixMs=1234567\n", data)
        self.assertEqual(replace.call_args_list, [call(temporary, path), call(temporary, path)])
        temporary.unlink.assert_called_once_with(missing_ok=True)

    def test_atomic_replace_timeout_removes_its_temporary_file(self):
        path, temporary = Mock(spec=Path), Mock(spec=Path)
        path.name = "notices.properties"
        path.with_name.return_value = temporary
        with patch("native_notices_qa.os.replace", side_effect=PermissionError("reader lock")), \
                patch("native_notices_qa.time.monotonic", side_effect=[0, 2, 3]):
            with self.assertRaises(PermissionError):
                atomic_write(path, b"original bytes")
        temporary.unlink.assert_called_once_with(missing_ok=True)

    def test_owned_fixture_cleanup_retries_child_directory_lock(self):
        qa = Path("unused-native-notice-qa").resolve()
        error = PermissionError("child working directory")
        error.winerror = 32
        with patch.object(Path, "exists", return_value=True), \
                patch.object(Path, "is_symlink", return_value=False), \
                patch.object(Path, "is_junction", return_value=False, create=True), \
                patch("native_notices_qa.os.walk", return_value=[]), \
                patch("native_notices_qa.shutil.rmtree", side_effect=[error, None]) as remove, \
                patch("native_notices_qa.time.monotonic", return_value=0), \
                patch("native_notices_qa.time.sleep"):
            remove_fixture(qa / "game", qa)
        self.assertEqual(remove.call_count, 2)


if __name__ == "__main__":
    unittest.main()
