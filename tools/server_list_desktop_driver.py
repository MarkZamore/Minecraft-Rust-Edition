"""Drive the two real-key sequences requested by an owned server_list_qa.py."""

import argparse
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import hashlib
import io
import json
import os
from pathlib import Path
import time

from PIL import Image


KEY_STEPS = ("Enter", "Delete", "F5", "F5", "Ctrl+A", "Backspace", "Enter")
KEYS = {"Enter": (0x0D,), "Delete": (0x2E,), "F5": (0x74,),
        "Ctrl+A": (0xA2, 0x41), "Backspace": (0x08,)}
SSIZE = c.c_size_t
CAPTURE_BUDGET = 48 * 1024 * 1024
CALLBACK = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


class ProcessEntry(c.Structure):
    _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                ("defaultHeap", SSIZE), ("module", w.DWORD), ("threads", w.DWORD),
                ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                ("exe", w.WCHAR * 260)]


class GuiThreadInfo(c.Structure):
    _fields_ = [("size", w.DWORD), ("flags", w.DWORD), ("active", w.HWND),
                ("focus", w.HWND), ("capture", w.HWND), ("menuOwner", w.HWND),
                ("moveSize", w.HWND), ("caret", w.HWND), ("rect", w.RECT)]


class BitmapInfo(c.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                ("imageBytes", w.DWORD), ("xPixelsPerMeter", w.LONG),
                ("yPixelsPerMeter", w.LONG), ("colors", w.DWORD),
                ("importantColors", w.DWORD)]


class KeyboardInput(c.Structure):
    _fields_ = [("virtualKey", w.WORD), ("scan", w.WORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("extra", SSIZE)]


class MouseInput(c.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG), ("data", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("extra", SSIZE)]


class InputValue(c.Union):
    _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]


class Input(c.Structure):
    _fields_ = [("kind", w.DWORD), ("value", InputValue)]


def functions(library, specs):
    library = c.WinDLL(library, use_last_error=True)
    for name, returns, parameters in specs:
        method = getattr(library, name)
        method.restype = returns
        method.argtypes = parameters
    return library


def unix_ms():
    return time.time_ns() // 1_000_000


class Desktop:
    def __init__(self):
        self.kernel = functions("kernel32", [
            ("OpenProcess", w.HANDLE, (w.DWORD, w.BOOL, w.DWORD)),
            ("CloseHandle", w.BOOL, (w.HANDLE,)),
            ("WaitForSingleObject", w.DWORD, (w.HANDLE, w.DWORD)),
            ("QueryFullProcessImageNameW", w.BOOL, (w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD))),
            ("GetProcessTimes", w.BOOL, (w.HANDLE, *(c.POINTER(w.FILETIME),) * 4)),
            ("CreateToolhelp32Snapshot", w.HANDLE, (w.DWORD, w.DWORD)),
            ("Process32FirstW", w.BOOL, (w.HANDLE, c.POINTER(ProcessEntry))),
            ("Process32NextW", w.BOOL, (w.HANDLE, c.POINTER(ProcessEntry))),
        ])
        self.user = functions("user32", [
            ("SetProcessDpiAwarenessContext", w.BOOL, (c.c_void_p,)),
            ("EnumWindows", w.BOOL, (CALLBACK, w.LPARAM)),
            ("GetWindowThreadProcessId", w.DWORD, (w.HWND, c.POINTER(w.DWORD))),
            ("IsWindowVisible", w.BOOL, (w.HWND,)),
            ("IsIconic", w.BOOL, (w.HWND,)),
            ("GetWindowTextW", c.c_int, (w.HWND, w.LPWSTR, c.c_int)),
            ("GetForegroundWindow", w.HWND, ()),
            ("SetForegroundWindow", w.BOOL, (w.HWND,)),
            ("GetGUIThreadInfo", w.BOOL, (w.DWORD, c.POINTER(GuiThreadInfo))),
            ("GetAncestor", w.HWND, (w.HWND, w.UINT)),
            ("GetClientRect", w.BOOL, (w.HWND, c.POINTER(w.RECT))),
            ("ClientToScreen", w.BOOL, (w.HWND, c.POINTER(w.POINT))),
            ("GetWindowRect", w.BOOL, (w.HWND, c.POINTER(w.RECT))),
            ("GetWindow", w.HWND, (w.HWND, w.UINT)),
            ("GetSystemMetrics", c.c_int, (c.c_int,)),
            ("GetDC", w.HDC, (w.HWND,)),
            ("ReleaseDC", c.c_int, (w.HWND, w.HDC)),
            ("GetAsyncKeyState", c.c_short, (c.c_int,)),
            ("SendInput", w.UINT, (w.UINT, c.POINTER(Input), c.c_int)),
        ])
        self.gdi = functions("gdi32", [
            ("CreateCompatibleDC", w.HDC, (w.HDC,)),
            ("CreateCompatibleBitmap", w.HBITMAP, (w.HDC, c.c_int, c.c_int)),
            ("SelectObject", w.HGDIOBJ, (w.HDC, w.HGDIOBJ)),
            ("BitBlt", w.BOOL, (w.HDC, c.c_int, c.c_int, c.c_int, c.c_int, w.HDC, c.c_int, c.c_int, w.DWORD)),
            ("GetDIBits", c.c_int, (w.HDC, w.HBITMAP, w.UINT, w.UINT, c.c_void_p, c.POINTER(BitmapInfo), w.UINT)),
            ("DeleteObject", w.BOOL, (w.HGDIOBJ,)),
            ("DeleteDC", w.BOOL, (w.HDC,)),
        ])
        self.dwm = functions("dwmapi", [
            ("DwmGetWindowAttribute", w.LONG, (w.HWND, w.DWORD, c.c_void_p, w.DWORD)),
        ])
        if not self.user.SetProcessDpiAwarenessContext(c.c_void_p(-4)):
            raise c.WinError(c.get_last_error())

    @contextmanager
    def process(self, pid):
        process = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not process:
            raise c.WinError(c.get_last_error())
        try:
            self.require_running(process)
            yield process
        finally:
            self.kernel.CloseHandle(process)

    def require_running(self, handle):
        if self.kernel.WaitForSingleObject(handle, 0) != 258:
            raise RuntimeError("An owned QA process exited")

    def executable(self, handle):
        path = c.create_unicode_buffer(32768)
        length = w.DWORD(len(path))
        if not self.kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(length)):
            raise c.WinError(c.get_last_error())
        return Path(path.value).resolve(strict=True)

    def started(self, handle):
        started, exited, kernel, user = (w.FILETIME() for _ in range(4))
        if not self.kernel.GetProcessTimes(handle, c.byref(started), c.byref(exited),
                                          c.byref(kernel), c.byref(user)):
            raise c.WinError(c.get_last_error())
        return ((started.dwHighDateTime << 32) | started.dwLowDateTime) // 10000 - 11644473600000

    def parent_pid(self, pid):
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = ProcessEntry(size=c.sizeof(ProcessEntry))
            found = self.kernel.Process32FirstW(snapshot, c.byref(entry))
            while found:
                if entry.pid == pid:
                    return entry.parent
                found = self.kernel.Process32NextW(snapshot, c.byref(entry))
            raise RuntimeError("The requested PID is absent from the live process snapshot")
        finally:
            self.kernel.CloseHandle(snapshot)

    def title(self, hwnd):
        value = c.create_unicode_buffer(1024)
        self.user.GetWindowTextW(hwnd, value, len(value))
        return value.value

    def owner(self, hwnd):
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, c.byref(pid))
        return pid.value

    def window(self, pid, title):
        found = []

        @CALLBACK
        def collect(hwnd, _):
            if self.owner(hwnd) == pid and self.user.IsWindowVisible(hwnd) \
                    and not self.user.IsIconic(hwnd) and self.title(hwnd) == title:
                found.append(hwnd)
            return True

        if not self.user.EnumWindows(collect, 0) or len(found) != 1:
            raise RuntimeError(f"Expected one visible owned window with title {title!r}, found {len(found)}")
        return found[0]

    def foreground(self, hwnd):
        thread = self.user.GetWindowThreadProcessId(hwnd, None)
        state = GuiThreadInfo(size=c.sizeof(GuiThreadInfo))
        return self.user.GetForegroundWindow() == hwnd and self.user.GetGUIThreadInfo(thread, c.byref(state)) \
            and bool(state.focus) and self.user.GetAncestor(state.focus, 2) == hwnd

    def activate(self, hwnd):
        deadline = time.monotonic() + 2
        while not self.foreground(hwnd):
            if time.monotonic() >= deadline:
                raise RuntimeError("Windows did not focus the owned game window")
            self.user.SetForegroundWindow(hwnd)
            time.sleep(0.05)

    def bounds(self, hwnd):
        rect, origin = w.RECT(), w.POINT()
        if not self.user.GetClientRect(hwnd, c.byref(rect)) or not self.user.ClientToScreen(hwnd, c.byref(origin)):
            raise c.WinError(c.get_last_error())
        x, y, right, bottom = (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)
        screen_x, screen_y, screen_w, screen_h = (self.user.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        if not (screen_x <= x < right <= screen_x + screen_w and screen_y <= y < bottom <= screen_y + screen_h
                and (right - x) * (bottom - y) <= 12_000_000):
            raise RuntimeError("The owned client is not fully within the bounded desktop capture area")
        # Only capture a client region with no visible window in front of it.
        # Exclude DWM-cloaked windows in other virtual desktops.
        visited = set()
        other = self.user.GetWindow(hwnd, 3)  # GW_HWNDPREV
        while other:
            if other in visited:
                raise RuntimeError("Window z-order changed during desktop validation")
            visited.add(other)
            cloak = w.DWORD()
            cloaked = self.dwm.DwmGetWindowAttribute(other, 14, c.byref(cloak), c.sizeof(cloak)) == 0 and cloak.value
            if not cloaked and self.user.IsWindowVisible(other) and not self.user.IsIconic(other):
                overlap = w.RECT()
                if not self.user.GetWindowRect(other, c.byref(overlap)):
                    raise RuntimeError("An overlapping desktop window changed during validation")
                if overlap.left < right and overlap.right > x and overlap.top < bottom and overlap.bottom > y:
                    raise RuntimeError("Another desktop window overlaps the owned game client")
            other = self.user.GetWindow(other, 3)
        return x, y, right, bottom

    def capture(self, bounds):
        x, y, right, bottom = bounds
        width, height = right - x, bottom - y
        display = self.user.GetDC(None)
        if not display:
            raise c.WinError(c.get_last_error())
        memory = bitmap = previous = None
        try:
            memory = self.gdi.CreateCompatibleDC(display)
            bitmap = self.gdi.CreateCompatibleBitmap(display, width, height)
            if not memory or not bitmap:
                raise c.WinError(c.get_last_error())
            previous = self.gdi.SelectObject(memory, bitmap)
            if not previous or not self.gdi.BitBlt(memory, 0, 0, width, height, display, x, y, 0x00CC0020):
                raise c.WinError(c.get_last_error())
            self.gdi.SelectObject(memory, previous)
            previous = None
            flags = BitmapInfo(size=c.sizeof(BitmapInfo), width=width, height=-height, planes=1, bits=32)
            pixels = c.create_string_buffer(width * height * 4)
            if self.gdi.GetDIBits(display, bitmap, 0, height, pixels, c.byref(flags), 0) != height:
                raise c.WinError(c.get_last_error())
            image = Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
            if not any(high > low for low, high in image.getextrema()):
                raise RuntimeError("Desktop capture showed no presented game pixels")
            result = io.BytesIO()
            image.save(result, format="PNG")
            return result.getvalue()
        finally:
            if previous:
                self.gdi.SelectObject(memory, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory:
                self.gdi.DeleteDC(memory)
            self.user.ReleaseDC(None, display)

    def press(self, hwnd, key):
        # Abort if a human is holding any modifier. One SendInput call queues
        # key releases with presses, so Ctrl does not remain held.
        if any(self.user.GetAsyncKeyState(modifier) & 0x8000
               for modifier in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise RuntimeError("A desktop modifier is already held")
        keys = KEYS[key]
        events = (Input * (2 * len(keys)))()
        for index, (vk, up) in enumerate([*((vk, False) for vk in keys),
                                           *((vk, True) for vk in reversed(keys))]):
            flags = (2 if up else 0) | (1 if vk == 0x2E else 0)
            events[index].kind = 1
            events[index].value.keyboard = KeyboardInput(virtualKey=vk, flags=flags)
        if not self.foreground(hwnd):
            raise RuntimeError("The owned window lost keyboard focus before input")
        sent = self.user.SendInput(len(events), events, c.sizeof(Input))
        if sent != len(events) or not self.foreground(hwnd):
            raise RuntimeError(f"Desktop input did not complete in the owned window ({sent}/{len(events)})")


def read_request(path, run, index, executable, title):
    if path.is_symlink() or path.stat().st_size > 4096:
        raise ValueError(f"Invalid desktop key checkpoint {path}")
    request = json.loads(path.read_text(encoding="utf-8"))
    identifier = f"run-{run}-key-{index}"
    key = KEY_STEPS[index - 1]
    if (request.get("id"), request.get("action"), request.get("key"), request.get("run"),
        request.get("title"), request.get("timeoutMs")) != (identifier, "press_key", key, run, title, 120_000):
        raise ValueError(f"Unexpected desktop key checkpoint {request!r}")
    pid, created = request.get("processId"), request.get("createdUnixMs")
    if type(pid) is not int or pid <= 0 or type(created) is not int or not created <= unix_ms() <= created + 120_000:
        raise ValueError(f"Invalid PID or expired desktop key checkpoint {request!r}")
    if Path(request["executable"]).resolve(strict=True) != executable:
        raise ValueError("The requested executable is not the owned QA candidate")
    return request


def await_request(path, desktop, qa_handle, deadline):
    while not path.is_file():
        desktop.require_running(qa_handle)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"No owned desktop key request arrived at {path}")
        time.sleep(0.05)


def service_request(desktop, request, qa_pid, qa_handle, executable, root, remaining):
    identifier, pid, key = request["id"], request["processId"], request["key"]
    ack = root / f"{identifier}.ack.json"
    screenshot = root / f"{identifier}.before.png"
    if ack.exists() or screenshot.exists():
        raise ValueError(f"Stale desktop evidence for {identifier}")
    with desktop.process(pid) as process:
        if desktop.parent_pid(pid) != qa_pid or desktop.executable(process) != executable \
                or not desktop.started(qa_handle) <= desktop.started(process) <= request["createdUnixMs"]:
            raise RuntimeError("The requested window is not a live child of this QA process")
        hwnd = desktop.window(pid, request["title"])
        desktop.activate(hwnd)
        bounds = desktop.bounds(hwnd)
        if not desktop.foreground(hwnd):
            raise RuntimeError("The owned client lost focus before capture")
        png = desktop.capture(bounds)
        captured_at = unix_ms()
        if len(png) > remaining:
            raise RuntimeError("The compact screenshot budget would be exceeded")
        with screenshot.open("xb") as output:
            output.write(png)
        desktop.require_running(qa_handle)
        desktop.require_running(process)
        if desktop.window(pid, request["title"]) != hwnd or desktop.bounds(hwnd) != bounds:
            raise RuntimeError("The owned desktop client changed after capture")
        if not captured_at <= unix_ms() <= request["createdUnixMs"] + request["timeoutMs"]:
            raise RuntimeError("The key request expired before desktop input")
        desktop.press(hwnd, key)
        performed = unix_ms()
    result = {"id": identifier, "key": key, "processId": pid, "status": "PASS", "performedUnixMs": performed,
              "hwnd": int(hwnd), "executable": str(executable), "title": request["title"],
              "screenshot": screenshot.name, "screenshotSha256": hashlib.sha256(png).hexdigest(),
              "capturedUnixMs": captured_at, "clientBounds": bounds}
    temp = ack.with_suffix(".tmp")
    with temp.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False) + "\n")
    try:
        temp.rename(ack)  # Windows rename refuses an existing acknowledgement.
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return remaining - len(png)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-pid", required=True, type=int, help="PID returned when starting server_list_qa.py")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=900)
    args = parser.parse_args()
    if os.name != "nt" or args.qa_pid <= 0 or not 1 <= args.seconds <= 1800:
        raise ValueError("A bounded Windows desktop QA session is required")
    candidate = args.candidate.resolve(strict=True)
    executable = (candidate / "native" / "horizon-client.exe").resolve(strict=True)
    title = json.loads((candidate / "native" / "presentation.json").read_text(encoding="utf-8"))["name"]
    requests = args.requests.resolve()
    stand = next((p for p in candidate.parents if p.parent.name == "agent-runs"
                  and p.parent.parent.name == "artifacts" and (p / "RUN.json").is_file()), None)
    if stand is None or not requests.is_relative_to(stand) or requests.is_relative_to(candidate) \
            or executable != candidate / "native" / "horizon-client.exe":
        raise ValueError("Candidate and request files must be in separate areas of one registered QA stand")

    desktop, deadline, remaining = Desktop(), time.monotonic() + args.seconds, CAPTURE_BUDGET
    with desktop.process(args.qa_pid) as qa_handle:
        if not desktop.executable(qa_handle).name.lower().startswith("python"):
            raise ValueError("The QA PID must be the owned Python runner")
        for run in (5, 6):
            owned_pid = None
            for index in range(1, len(KEY_STEPS) + 1):
                path = requests / f"run-{run}-key-{index}.request.json"
                await_request(path, desktop, qa_handle, deadline)
                request = read_request(path, run, index, executable, title)
                if owned_pid is not None and request["processId"] != owned_pid:
                    raise RuntimeError("The process changed within one QA launch")
                owned_pid = request["processId"]
                remaining = service_request(desktop, request, args.qa_pid, qa_handle, executable, requests, remaining)


if __name__ == "__main__":
    main()
