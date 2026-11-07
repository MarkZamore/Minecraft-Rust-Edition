"""Verify launcher notices in the exact, visible Windows native client.

Own technical profile only. A real bootstrap child renders the cards; controller
PNGs, pixel measurements, HWND geometry and post-present heartbeats are independent
evidence. No screenshot is synthesized and a heartbeat alone cannot produce PASS.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from PIL import Image, ImageChops

from release_acceptance import execution_context
from stage_runtime import file_info


WORLD = "RustNoticeTechnical"
COLOR = (227, 17, 197)
CARD_WIDTH = 240
GAP = 12
LIFE_MS = 7000
STYLE = {
    "bgArgb": "FFE311C5", "borderArgb": "FFE311C5", "textArgb": "FF000000",
    "mutedArgb": "FF000000", "fontFamily": "Segoe UI", "fontSize": 16,
    "width": CARD_WIDTH, "gap": GAP, "padding": 12, "radius": 0,
    "riseMs": 0, "fadeMs": 0, "lifeMs": LIFE_MS,
}


def unix_ms():
    return time.time_ns() // 1_000_000


def properties(values):
    def escape(value):
        return (str(value).replace("\\", "\\\\").replace("\n", "\\n")
                .replace("\r", "\\r").replace("\t", "\\t")
                .replace("=", "\\=").replace(":", "\\:"))
    return ("".join(f"{key}={escape(value)}\n" for key, value in values.items())).encode("utf-8")


def retry_file_lock(operation, timeout=1):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return operation()
        except OSError as error:
            if (not isinstance(error, PermissionError) and getattr(error, "winerror", None) not in (32, 33)) \
                    or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def atomic_write(path, data):
    temporary = path.with_name(path.name + ".qa.tmp")
    try:
        # A Windows reader may briefly deny replacement. Retry these same
        # already serialized bytes, preserving the notice's original lifetime.
        temporary.write_bytes(data)
        retry_file_lock(lambda: os.replace(temporary, path))
    finally:
        retry_file_lock(lambda: temporary.unlink(missing_ok=True))


def snapshot(count, created=None, dpi=1):
    created = unix_ms() if created is None else created
    result = {"count": count, "dpiScale": dpi}
    for index in range(count):
        result.update({f"{index}.title": f"Проверка {index + 1}",
                       f"{index}.body": "Видимое уведомление • Rust QA",
                       f"{index}.createdUnixMs": created})
    return properties(result), created


def heartbeat(path):
    # Heartbeats contain fixed ASCII keys/numbers and an empty healthy error.
    text = path.read_text(encoding="utf-8")
    result = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    for key in ("pid", "timestampUnixMs", "renderedCount", "transformations"):
        result[key] = int(result[key])
    return result


def colored_cards(image, minimum_width=100):
    """Find large runs of the unique opaque QA background, not game text dots."""
    rgb = image.convert("RGB")
    # Final readback may apply the framebuffer's sRGB transfer. Accept that
    # defined transfer as well as literal RGBA bytes, never arbitrary colours.
    srgb = lambda value: round(255 * (12.92 * value if value <= 0.0031308
                                      else 1.055 * value ** (1 / 2.4) - 0.055))
    palettes = [COLOR, tuple(srgb(value / 255) for value in COLOR)]
    mask = Image.new("L", rgb.size, 0)
    for palette in palettes:
        candidate = Image.new("L", rgb.size, 255)
        for channel, wanted in zip(rgb.split(), palette):
            band = channel.point(lambda value: 255 if abs(value - wanted) <= 8 else 0)
            candidate = ImageChops.multiply(candidate, band)
        mask = ImageChops.lighter(mask, candidate)
    rows = []
    for y in range(image.height):
        row = mask.crop((0, y, image.width, y + 1))
        if row.histogram()[255] >= minimum_width:
            rows.append(y)
    groups = []
    for y in rows:
        if not groups or y > groups[-1][-1] + 1:
            groups.append([y])
        else:
            groups[-1].append(y)
    cards = []
    for group in groups:
        if len(group) < 20:
            continue
        box = mask.crop((0, group[0], image.width, group[-1] + 1)).getbbox()
        cards.append([box[0], group[0], box[2], group[-1] + 1])
    return cards


def measure_pixels(image, count, dpi):
    cards = colored_cards(image)
    if len(cards) != count:
        raise AssertionError(f"Real screenshot contains {len(cards)} QA cards, expected {count}")
    expected = min(round(CARD_WIDTH * dpi), image.width - 2 * round(GAP * dpi))
    for left, top, right, bottom in cards:
        if abs(right - left - expected) > 5:
            raise AssertionError(f"Card width {right-left} does not match physical width {expected}")
        if abs(left - round(GAP * dpi)) > 5:
            raise AssertionError("Card is not at the physical lower-left margin")
    if cards and abs(image.height - cards[-1][3] - round(GAP * dpi)) > 5:
        raise AssertionError("Bottom card is not at the physical bottom margin")
    return {"count": len(cards), "boxes": cards, "size": list(image.size),
            "expectedPhysicalWidth": expected}


def ui_shape(value):
    return {"screen": value.get("screen"), "widgets": [
        {key: item[key] for key in ("id", "bounds", "label", "enabled") if key in item}
        for item in value.get("widgets", [])]}


def registered_output(path):
    path = path.resolve()
    for parent in path.parents:
        if (parent / "RUN.json").is_file() and parent.parent.name == "agent-runs" \
                and parent.parent.parent.name == "artifacts":
            json.loads((parent / "RUN.json").read_text(encoding="utf-8-sig"))
            if path.exists():
                raise ValueError("Use a fresh notices QA directory; previous evidence must be reviewed first")
            return path
    raise ValueError("--out must be inside a registered artifacts/agent-runs/<task>/ stand")


def remove_fixture(path, qa):
    if path.parent.resolve() != qa.resolve() or path.name not in ("game", "session", "empty-profile"):
        raise ValueError("Refusing cleanup outside owned technical fixture")
    def remove():
        if not path.exists():
            return
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Refusing cleanup through a fixture link")
        for directory, names, files in os.walk(path, followlinks=False):
            for name in [*names, *files]:
                item = Path(directory) / name
                if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                    raise ValueError("Refusing cleanup through a fixture link")
        shutil.rmtree(path)

    # The bootstrap can exit before its killed child releases its working
    # directory. Recheck the exact owned path on every bounded cleanup retry.
    retry_file_lock(remove, timeout=5)


def process_ancestry(renderer_pid, root_pid, client):
    # Read only ancestors of the heartbeat PID. The still-running Popen root is
    # also checked by the caller, preventing adoption of an unrelated/reused PID.
    script = (
        f"$pidToRead={renderer_pid:d}; $rootToFind={root_pid:d}; $rows=@(); "
        "for($i=0;$i -lt 12 -and $pidToRead -gt 0;$i++){ "
        "$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$pidToRead); "
        "if(!$p){break}; $rows+=@{pid=[int]$p.ProcessId; parent=[int]$p.ParentProcessId; "
        "name=$p.Name; path=$p.ExecutablePath; created=$p.CreationDate.ToUniversalTime().ToString('o')}; "
        "if($pidToRead -eq $rootToFind){break}; $pidToRead=[int]$p.ParentProcessId }; "
        "ConvertTo-Json -InputObject @($rows) -Compress"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=20, check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    rows = json.loads(result.stdout)
    if not rows or rows[0]["pid"] == root_pid or rows[-1]["pid"] != root_pid:
        raise AssertionError("Heartbeat PID is not a real child of the owned bootstrap")
    if Path(rows[0]["path"]).resolve() != client.resolve() or rows[0]["name"] != client.name:
        raise AssertionError("Heartbeat came from another executable")
    return rows


class WindowProbe:
    """Read native window/client/monitor geometry without changing focus or DPI."""

    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = (self.callback_type, wintypes.LPARAM)
        self.user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        self.user.IsWindowVisible.argtypes = (wintypes.HWND,)
        self.user.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.user.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        self.user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
        self.user.MonitorFromWindow.restype = wintypes.HMONITOR
        self.user.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.c_void_p)
        # Physical HWND coordinates in this QA process only; no game setting changes.
        try:
            self.user.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
            self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            pass

    def inspect(self, pid):
        windows = []

        @self.callback_type
        def visit(hwnd, _):
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            rectangle = wintypes.RECT()
            if owner.value == pid and self.user.IsWindowVisible(hwnd) \
                    and self.user.GetClientRect(hwnd, ctypes.byref(rectangle)) \
                    and rectangle.right >= 640 and rectangle.bottom >= 360:
                windows.append(hwnd)
            return True

        self.user.EnumWindows(visit, 0)
        if len(windows) != 1:
            raise AssertionError(f"Expected one owned rendering window, found {len(windows)}")
        hwnd = windows[0]
        rectangle, outer = wintypes.RECT(), wintypes.RECT()
        self.user.GetClientRect(hwnd, ctypes.byref(rectangle))
        point = wintypes.POINT(0, 0)
        self.user.ClientToScreen(hwnd, ctypes.byref(point))
        self.user.GetWindowRect(hwnd, ctypes.byref(outer))
        title = ctypes.create_unicode_buffer(1024)
        self.user.GetWindowTextW(hwnd, title, len(title))

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        monitor = MonitorInfo()
        monitor.cbSize = ctypes.sizeof(monitor)
        handle = self.user.MonitorFromWindow(hwnd, 2)
        if not self.user.GetMonitorInfoW(handle, ctypes.byref(monitor)):
            raise ctypes.WinError(ctypes.get_last_error())
        as_list = lambda value: [value.left, value.top, value.right, value.bottom]
        return {"hwnd": int(hwnd), "title": title.value,
                "client": [point.x, point.y, point.x + rectangle.right, point.y + rectangle.bottom],
                "window": as_list(outer), "monitor": as_list(monitor.monitor),
                "foregroundHwnd": int(self.user.GetForegroundWindow() or 0)}


class Session:
    def __init__(self, candidate, controller, cache, qa):
        self.candidate, self.controller, self.cache, self.qa = candidate, controller, cache, qa
        self.client = candidate / "native/horizon-client.exe"
        self.bootstrap = candidate / "native/launch-rust-edition.exe"
        self.game, self.directory = qa / "game", qa / "session"
        self.endpoint = qa / "control.json"
        self.process, self.log = None, None
        self.renderer_pid = None
        self.window = WindowProbe()
        self.captures = []

    def start(self):
        self.game.mkdir()
        self.directory.mkdir()
        (self.game / "options.txt").write_text(
            "guiScale:2\nnarrator:0\nfullscreen:false\nlang:en_us\n", encoding="utf-8")
        self.sentinel = b'{"owner":"native-notices-qa","doNotRewrite":true}\n'
        (self.directory / "session.json").write_bytes(self.sentinel)
        atomic_write(self.directory / "style.properties", properties(STYLE))
        self.send(0)
        environment = dict(os.environ)
        for key in tuple(environment):
            if key.upper().startswith(("LANMINECRAFT_", "HORIZON_")):
                del environment[key]
        # Reapply after isolation: smoke_runtime.run_client deliberately removes
        # LANMINECRAFT_* and cannot be used for this contract test.
        environment.update(LANMINECRAFT_NOTICE_DIRECTORY=str(self.directory),
                           LANMINECRAFT_NATIVE_CACHE=str(self.cache),
                           APPDATA=str(self.qa / "empty-profile"))
        self.arguments = [str(self.bootstrap), "--game-dir", str(self.game),
                          "--control-socket", str(self.endpoint)]
        self.log = (self.qa / "client.log").open("wb")
        self.process = subprocess.Popen(self.arguments, cwd=self.game, env=environment,
                                        stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 1200
        while not self.endpoint.is_file():
            self.alive()
            if time.monotonic() >= deadline:
                raise TimeoutError("Bootstrap did not open the native control endpoint")
            time.sleep(0.25)
        self.control("wait-for", "screen", "title", "--timeout", "90")
        first = self.wait_heartbeat(0)
        self.renderer_pid = first["pid"]
        self.ancestry = process_ancestry(self.renderer_pid, self.process.pid, self.client)

    def alive(self):
        if self.process.poll() is not None:
            raise RuntimeError(f"Owned bootstrap exited with code {self.process.returncode}; see client.log")

    def control(self, *words, timeout=120):
        self.alive()
        result = subprocess.run([str(self.controller), "--socket", str(self.endpoint), *map(str, words)],
                                cwd=self.game, capture_output=True, encoding="utf-8",
                                timeout=timeout, check=True)
        value = json.loads(result.stdout)
        if isinstance(value, dict) and "error" in value:
            raise RuntimeError(value["error"])
        return value

    def send(self, count, created=None, dpi=1):
        data, created = snapshot(count, created, dpi)
        atomic_write(self.directory / "notices.properties", data)
        return created

    def wait_heartbeat(self, count, after=0, timeout=5):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            self.alive()
            try:
                value = heartbeat(self.directory / "heartbeat.properties")
                last = value
                now = unix_ms()
                if value.get("backend") == "native-ui-v1" and not value.get("error") \
                        and value["renderedCount"] == count and value["transformations"] == 0 \
                        and after < value["timestampUnixMs"] <= now \
                        and now - value["timestampUnixMs"] <= 1500 \
                        and (self.renderer_pid is None or value["pid"] == self.renderer_pid):
                    return value
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.1)
        raise TimeoutError(f"No fresh healthy native presentation heartbeat with count={count}; last={last!r}")

    def capture(self, name, count, dpi=1):
        before = self.wait_heartbeat(count)
        before_observed = unix_ms()
        path = self.qa / f"{name}.png"
        # Unscaled screenshot reads the final presented renderer target including
        # notices; a --scaled/offscreen capture would not test the native frontend.
        self.control("screenshot", path)
        captured = unix_ms()
        with Image.open(path) as frame:
            measurement = measure_pixels(frame, count, dpi)
        after = self.wait_heartbeat(count, after=before["timestampUnixMs"])
        after_observed = unix_ms()
        geometry = self.window.inspect(self.renderer_pid)
        size = [geometry["client"][2] - geometry["client"][0],
                geometry["client"][3] - geometry["client"][1]]
        if measurement["size"] != size:
            raise AssertionError("Screenshot does not match the actual native client rectangle")
        result = {"file": path.name, **file_info(path), "pixels": measurement,
                  "capturedUnixMs": captured, "heartbeatBeforeObservedUnixMs": before_observed,
                  "heartbeatAfterObservedUnixMs": after_observed,
                  "heartbeatBefore": before, "heartbeatAfter": after, "window": geometry}
        self.captures.append(result)
        return result

    def until(self, stamp):
        while unix_ms() < stamp:
            self.alive()
            time.sleep(max(0, min(0.1, (stamp - unix_ms()) / 1000)))

    def stop(self, success):
        try:
            if self.process is not None and self.process.poll() is None and success:
                self.control("quit", timeout=30)
                if self.process.wait(timeout=60) != 0:
                    raise RuntimeError("Bootstrap/native client did not exit cleanly")
        finally:
            if self.process is not None and self.process.poll() is None:
                # Only the Popen-owned root and its current children; never an
                # executable-name-wide process kill or a previously reused PID.
                subprocess.run(["taskkill.exe", "/PID", str(self.process.pid), "/T", "/F"],
                               capture_output=True, timeout=20, check=False)
                self.process.wait(timeout=20)
            if self.log is not None:
                self.log.close()


def check_mods(session):
    session.control("options", "mods")
    page = session.control("ui")
    ids = {item["id"] for item in page["widgets"]}
    if ids != {"browse-mods", "open-mod-folder", "done"}:
        raise AssertionError("Launcher notices changed the reviewed empty native mod inventory")
    # Opening an options subpage pushes Mods above Main. Leave both pages so
    # the following title/world checks measure the real screen, not its options.
    for _ in range(3):
        page = session.control("ui")
        done = [item for item in page["widgets"] if item["id"] == "done"]
        if not done:
            return sorted(ids), ui_shape(page)
        if len(done) != 1 or not done[0]["enabled"]:
            raise AssertionError("The technical Mods inventory has no usable Done action")
        session.control("click", done[0]["id"])
    page = session.control("ui")
    if any(item["id"] == "done" for item in page["widgets"]):
        raise AssertionError("The technical Mods inventory did not return from options")
    return sorted(ids), ui_shape(page)


def require_main_title(page):
    widgets = page.get("widgets", [])
    labels = {item.get("label") for item in widgets if item.get("enabled") is True}
    if page.get("screen") != "title" or not {"Singleplayer", "Multiplayer"}.issubset(labels) \
            or any(item.get("id") in {"browse-mods", "open-mod-folder", "done"} for item in widgets):
        raise AssertionError("Title proof is not the actual main title screen")


def exercise(session, report):
    checks = report["checks"]
    initial_mods, title_ui = check_mods(session)
    require_main_title(title_ui)
    report["titleUiBefore"] = title_ui
    expected_title = json.loads((session.candidate / "native/presentation.json").read_text(encoding="utf-8"))["name"]
    if session.window.inspect(session.renderer_pid)["title"] != expected_title:
        raise AssertionError("The exact native child has the wrong window title")
    checks["bootstrapChildPidAndTitle"] = "PASS"
    report["processAncestry"] = session.ancestry
    session.send(1)
    title = session.capture("title-one", 1)
    if session.control("ui")["screen"] != "title":
        raise AssertionError("Notice stole the title screen")
    checks["titleVisiblePixelsAndFreshPresentation"] = "PASS"

    created = session.send(3)
    timing = report["timing"] = {"lifeMs": LIFE_MS}
    lifetime = timing["threeCards"] = {"createdUnixMs": created, "sentUnixMs": unix_ms()}
    session.capture("title-three", 3)
    session.until(created + 6000)
    near = session.wait_heartbeat(3, after=created + 5999)
    lifetime["nearExpiry"] = {"heartbeat": near, "observedUnixMs": unix_ms()}
    session.until(created + LIFE_MS)
    expired = session.wait_heartbeat(0, after=created + LIFE_MS - 1)
    lifetime["expired"] = {"heartbeat": expired, "observedUnixMs": unix_ms()}
    session.capture("expired-three", 0)
    checks["threeCardsAndSevenSecondExpiry"] = "PASS"

    created = session.send(1)
    torn = timing["tornSnapshot"] = {"createdUnixMs": created, "sentUnixMs": unix_ms(),
                                      "invalidSnapshots": []}
    session.wait_heartbeat(1)
    for delay, kind, data in (
            (2000, "invalid-utf8", b"count=0\ndpiScale=1\n\xff"),
            (4000, "broken-unicode-escape", b"count=3\ndpiScale=1\n0.title=broken\\u12\n")):
        session.until(created + delay)
        atomic_write(session.directory / "notices.properties", data)
        written = unix_ms()
        time.sleep(0.7)
        retained = session.wait_heartbeat(1, after=written + 499)
        torn["invalidSnapshots"].append({"kind": kind, "writtenUnixMs": written,
                                          "heartbeat": retained, "observedUnixMs": unix_ms()})
    session.capture("torn-snapshot-retains-card", 1)
    session.until(created + LIFE_MS)
    expired = session.wait_heartbeat(0, after=created + LIFE_MS - 1)
    torn["expired"] = {"heartbeat": expired, "observedUnixMs": unix_ms()}
    session.capture("torn-snapshot-original-expiry", 0)
    checks["malformedAndTornKeepPriorUntilOriginalExpiry"] = "PASS"

    queued = unix_ms()
    created = session.send(1, queued + 1500)
    future = timing["futureNotice"] = {"queuedUnixMs": queued, "createdUnixMs": created,
                                       "sentUnixMs": unix_ms()}
    session.wait_heartbeat(0, after=future["sentUnixMs"])
    session.capture("future-not-yet-visible", 0)
    if unix_ms() >= created:
        raise AssertionError("Future-skew precondition was not observed before its creation time")
    session.until(created + 100)
    session.wait_heartbeat(1)
    session.capture("future-notice-retained", 1)
    checks["future1500msRetainedUntilItsTime"] = "PASS"

    normal_created = session.send(1, dpi=1)
    session.until(normal_created + 700)
    normal = session.capture("dpi-one", 1)
    ui_before = ui_shape(session.control("ui"))
    options_before = (session.game / "options.txt").read_text(encoding="utf-8")
    double_created = session.send(1, dpi=2)
    # Count stays one, so an immediately observed heartbeat could still belong
    # to the preceding 250-ms worker snapshot. Wait beyond that poll interval.
    session.until(double_created + 700)
    double = session.capture("dpi-two", 1, 2)
    ui_after = ui_shape(session.control("ui"))
    if ui_after != ui_before:
        raise AssertionError("Notice DPI changed Minecraft widget geometry")
    gui = lambda text: next(line for line in text.splitlines() if line.startswith("guiScale:"))
    gui_before = gui(options_before)
    gui_after = gui((session.game / "options.txt").read_text(encoding="utf-8"))
    if gui_after != gui_before:
        raise AssertionError("Notice DPI rewrote the game's GUI scale")
    first_width = normal["pixels"]["boxes"][0][2] - normal["pixels"]["boxes"][0][0]
    second_width = double["pixels"]["boxes"][0][2] - double["pixels"]["boxes"][0][0]
    if abs(second_width - 2 * first_width) > 5:
        raise AssertionError("Notice physical width did not scale exactly once at DPI 2")
    report["dpi"] = {"oneWidth": first_width, "twoWidth": second_width,
                     "guiScale": gui_before, "guiScaleBefore": gui_before, "guiScaleAfter": gui_after,
                     "uiBefore": ui_before, "uiAfter": ui_after}
    checks["dpiOnceAndGameGuiScaleUnchanged"] = "PASS"
    report["titleUiAfter"] = ui_shape(session.control("ui"))
    require_main_title(report["titleUiAfter"])
    if report["titleUiAfter"] != title_ui:
        raise AssertionError("Native notices changed the actual main title widgets")

    session.send(0)
    session.wait_heartbeat(0)
    session.control("open", WORLD, "137248", "creative")
    session.control("wait-for", "loaded", "--timeout", "120")
    deadline = time.monotonic() + 120
    while (server := session.control("status").get("server")) and not server["loaded"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("The technical world did not finish its integrated connection")
        time.sleep(0.1)
    session.control("key", "f11", "tap")
    deadline = time.monotonic() + 12
    while True:
        geometry = session.window.inspect(session.renderer_pid)
        if all(abs(a-b) <= 2 for a, b in zip(geometry["client"], geometry["monitor"])):
            break
        if time.monotonic() >= deadline:
            raise AssertionError("F11 did not produce an actual monitor-sized native client")
        time.sleep(0.1)
    session.send(1)
    session.capture("fullscreen-world", 1)
    checks["actualFullscreenVisibleNotice"] = "PASS"
    session.send(1)
    chat_window = session.window.inspect(session.renderer_pid)
    before = chat_window["foregroundHwnd"]
    report["foregroundBeforeChat"] = before
    if before <= 0 or before != chat_window["hwnd"]:
        raise AssertionError("The tested native game window is not foreground before chat input")
    chat_before = session.control("chat-log", "8")["chat"]
    session.control("key", "t", "tap")
    session.control("type", "/list")
    session.capture("world-chat-input-with-card", 1)
    session.control("key", "enter", "tap")
    deadline = time.monotonic() + 10
    while True:
        chat = session.control("chat-log", "8")["chat"]
        if chat != chat_before and "online" in str(chat).casefold():
            break
        if time.monotonic() >= deadline:
            raise AssertionError("T/type/Enter /list did not reach the integrated server with a card present")
        time.sleep(0.1)
    after_window = session.window.inspect(session.renderer_pid)
    after = after_window["foregroundHwnd"]
    report["foregroundAfterChat"] = after
    if before != after or after != after_window["hwnd"] or after_window["hwnd"] != chat_window["hwnd"]:
        raise AssertionError("Foreground input focus changed during notice/chat input")
    report["technicalChat"] = chat
    checks["worldAndChatInputWhileVisible"] = "PASS"
    session.send(0)
    session.wait_heartbeat(0)
    session.control("key", "esc", "tap")
    session.control("wait-for", "screen", "paused", "--timeout", "5")
    pause = ui_shape(session.control("ui"))
    report["pauseUiBefore"] = pause
    session.send(3)
    session.capture("paused-world-three", 3)
    pause_after = ui_shape(session.control("ui"))
    report["pauseUiAfter"] = pause_after
    if pause_after != pause:
        raise AssertionError("Notice cards changed pause-menu widgets/input")
    checks["pauseMenuVisibleWithoutInputWidgets"] = "PASS"
    session.send(0)
    cleared = session.wait_heartbeat(0)
    session.capture("paused-zero-fresh-heartbeat", 0)
    if cleared["timestampUnixMs"] <= title["heartbeatAfter"]["timestampUnixMs"]:
        raise AssertionError("An empty notice queue did not retain fresh presentation heartbeats")
    checks["emptyQueueFreshHeartbeat"] = "PASS"
    if check_mods(session)[0] != initial_mods:
        raise AssertionError("Native mod inventory changed after displaying notices")
    if (session.directory / "session.json").read_bytes() != session.sentinel:
        raise AssertionError("Native frontend rewrote launcher-owned session.json")
    checks["sessionSentinelAndNoExtraMod"] = "PASS"
    final_ancestry = process_ancestry(session.renderer_pid, session.process.pid, session.client)
    if final_ancestry != session.ancestry:
        raise AssertionError("The verified native process ancestry changed during QA")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path,
                        help="Existing verified bootstrap cache; resources are shared read-only inputs")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise ValueError("This acceptance requires the real Windows x64 client")
    candidate, controller, cache = (value.resolve(strict=True) for value in
                                    (args.candidate, args.controller, args.cache))
    qa = registered_output(args.out)
    context = execution_context(candidate)
    qa.mkdir(parents=True)
    session = Session(candidate, controller, cache, qa)
    report = {"schemaVersion": 1, "status": "FAIL", "acceptanceContext": context,
              "client": file_info(session.client), "bootstrap": file_info(session.bootstrap),
              "startup": "exact bootstrap child; native-ui-v1; real presented unscaled screenshots",
              "checks": {}, "captures": session.captures, "fixtureRemoved": False}
    success = False
    try:
        session.start()
        report["arguments"] = session.arguments
        report["nativeProcessId"] = session.renderer_pid
        report["bootstrapProcessId"] = session.process.pid
        exercise(session, report)
        if execution_context(candidate) != context:
            raise AssertionError("Candidate payload/receipt changed during native notice QA")
        report["checks"]["exactCandidateUnchanged"] = "PASS"
        success = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        try:
            session.stop(success)
        except Exception as error:
            success = False
            report["error"] = f"{report.get('error', '')}; cleanup/exit: {error}".strip("; ")
        # Worlds, settings, IPC token and synthetic queue are always recoverable.
        # Retain only bounded screenshots, compact report and diagnostic log.
        cleanup_errors = []
        for folder in (session.game, session.directory, qa / "empty-profile"):
            try:
                if folder.exists():
                    remove_fixture(folder, qa)
            except Exception as error:
                cleanup_errors.append(f"{folder.name}: {error}")
        try:
            session.endpoint.unlink(missing_ok=True)
        except OSError as error:
            cleanup_errors.append(f"control endpoint: {error}")
        if cleanup_errors:
            success = False
            report["cleanupErrors"] = cleanup_errors
        report["fixtureRemoved"] = not cleanup_errors
        report["status"] = "PASS" if success else "FAIL"
        report["retainedBytes"] = sum(path.stat().st_size for path in qa.rglob("*") if path.is_file())
        (qa / "native-notices-qa.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not success:
        raise RuntimeError("Native notice acceptance failed; see native-notices-qa.json")


if __name__ == "__main__":
    main()
