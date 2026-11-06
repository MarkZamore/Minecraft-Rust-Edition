"""Create, save and reopen one technical world in the actual Windows client."""

import argparse
import json
import os
import pathlib
import subprocess
import time

from stage_runtime import file_info


WORLD = "RustEditionSmoke"
PLAYER = "RustEditionQA"


def check_window_title(pid, expected):
    # Inspect only the game launched by this test. The bootstrap is its direct parent.
    script = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid:d}' | "
        "Where-Object Name -eq 'horizon-client.exe'; "
        "if ($p) { (Get-Process -Id $p.ProcessId -ErrorAction Stop).MainWindowTitle }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.stdout.strip() != expected:
        raise RuntimeError(f"Expected window title {expected!r}, got {result.stdout.strip()!r}")


def run_client(executable, controller, resources, qa, index, action, standalone=None,
               expected_title=None):
    game = qa / "game"
    game.mkdir(exist_ok=True)
    endpoint = qa / f"control-{index}.json"
    if endpoint.exists():
        raise ValueError("Use a fresh smoke run directory")
    environment = dict(os.environ, HORIZON_NO_DISCORD="1", HORIZON_TEST_LOOPBACK="1")
    for name in tuple(environment):
        if name.upper().startswith("LANMINECRAFT_"):
            del environment[name]
    arguments = [str(executable), "--resources", str(resources), "--home", str(game), "--name", PLAYER]
    if standalone:
        bootstrap, minecraft, java, cache = standalone
        arguments = [str(bootstrap), "--minecraft-dir", str(minecraft), "--java-home", str(java)]
        environment["LANMINECRAFT_NATIVE_CACHE"] = str(cache)
    arguments.extend(["--game-dir", str(game), "--control-socket", str(endpoint)])
    with (qa / f"client-{index}.log").open("wb") as output:
        process = subprocess.Popen(arguments, cwd=game, env=environment, stdout=output, stderr=output)

        def control(*args, timeout=120):
            result = subprocess.run(
                [str(controller), "--socket", str(endpoint), *args],
                cwd=game, capture_output=True, encoding="utf-8", timeout=timeout, check=True,
            )
            reply = json.loads(result.stdout)
            if isinstance(reply, dict) and "error" in reply:
                raise RuntimeError(reply["error"])
            return reply

        try:
            deadline = time.monotonic() + (360 if standalone else 90)
            while not endpoint.is_file():
                if process.poll() is not None:
                    raise RuntimeError(f"Native client exited before ready, code {process.returncode}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Native client did not create its control endpoint")
                time.sleep(0.25)
            control("wait-for", "screen", "title", "--timeout", "90")
            if expected_title is not None:
                check_window_title(process.pid, expected_title)
            result = action(control)
            control("quit", timeout=20)
            if process.wait(timeout=45) != 0:
                raise RuntimeError(f"Native client exited with {process.returncode}")
            return result
        finally:
            if process.poll() is None:
                if standalone:
                    subprocess.run(
                        ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True, timeout=15,
                    )
                else:
                    process.kill()
                process.wait(timeout=15)


def check_pause_menu(control):
    def button(screen, labels):
        return next((widget for widget in screen["widgets"] if widget.get("label") in labels), None)

    def rendered_pause():
        deadline = time.monotonic() + 5
        screen = control("ui")
        while screen["screen"] == "paused" and not screen["widgets"] and time.monotonic() < deadline:
            time.sleep(0.05)
            screen = control("ui")
        return screen

    open_labels = {"Open to LAN", "Открыть для сети"}
    report_labels = {"Player Reporting", "Жалобы"}
    # The general loaded condition can be true before the integrated
    # connection finishes its own level-loading overlay.
    deadline = time.monotonic() + 90
    while (server := control("status").get("server")) and not server["loaded"]:
        if time.monotonic() >= deadline:
            raise TimeoutError("The integrated connection did not finish loading")
        time.sleep(0.1)
    control("key", "esc", "tap")
    control("wait-for", "screen", "paused", "--timeout", "5")
    before = rendered_pause()
    opened = button(before, open_labels)
    if before["screen"] != "paused" or not opened or not opened["enabled"]:
        raise RuntimeError(f"The pause menu does not offer Open to LAN: {before!r}")
    if any(widget.get("label", "").startswith(("World Settings", "World Options", "Настройки мира"))
           for widget in before["widgets"]):
        raise RuntimeError("The pause menu still offers World Settings")
    if control("click", opened["id"])["screen"] != "paused":
        raise RuntimeError("Opening to LAN left the pause menu")
    after = rendered_pause()
    reports = button(after, report_labels)
    if after["screen"] != "paused" or not reports or not reports["enabled"]:
        raise RuntimeError("Opening to LAN did not directly replace the pause button")
    if reports["bounds"] != opened["bounds"] or button(after, open_labels):
        raise RuntimeError("Player Reporting did not replace the same pause slot")
    control("click", reports["id"])
    if control("ui")["screen"] != "social":
        raise RuntimeError("Player Reporting did not open the social screen")
    control("key", "esc", "tap")
    control("resume")
    return {"before": opened["label"], "after": reports["label"], "loopback": "PASS"}


def set_test_language(game, code):
    game.mkdir(exist_ok=True)
    options = game / "options.txt"
    lines = options.read_text(encoding="utf-8").splitlines() if options.is_file() else []
    lines = [line for line in lines if not line.startswith("lang:")]
    options.write_text("\n".join([*lines, f"lang:{code}"]) + "\n", encoding="utf-8")


def smoke(executable, controller, resources, qa, standalone=None, expected_title=None,
          expect_cold=False, reuse_world=False, check_pause=False):
    suffix = "-update" if reuse_world else ""
    first_index = 3 if reuse_world else 1
    second_index = first_index + 1
    preview = qa / f"preview{suffix}.png"
    saved = qa / "game" / "saves" / WORLD / "level.dat"
    if reuse_world and not saved.is_file():
        raise ValueError("No technical world from the previous smoke run")
    if check_pause:
        set_test_language(qa / "game", "en_us")

    def create(control):
        control("options", "mods")
        mods = control("ui")
        if {widget["id"] for widget in mods["widgets"]} != {"open-mod-folder", "done"}:
            raise RuntimeError("The native Mods page has entries outside the expected empty inventory")
        control("click", "done")
        world = [WORLD] if reuse_world else [WORLD, "137248", "creative"]
        control("open", *world)
        photograph(control, preview)
        control("save")
        return control("state"), mods

    def reopen(control):
        worlds = control("worlds")
        if not saved.is_file() or saved.stat().st_size == 0:
            raise RuntimeError("Native client did not create a saved world")
        control("open", WORLD)
        control("wait-for", "loaded", "--timeout", "90")
        return worlds, control("state")

    (first, mods) = run_client(
        executable, controller, resources, qa, first_index, create, standalone, expected_title,
    )
    cache_receipt = standalone[3] / "resources.json" if standalone else None
    first_receipt = file_info(cache_receipt) if cache_receipt else None
    worlds, reopened = run_client(
        executable, controller, resources, qa, second_index, reopen, standalone, expected_title,
    )
    if any(first[key] != reopened[key] for key in ("dimension", "gamemode")) or any(
        abs(a - b) > 1.0 for a, b in zip(first["player"]["position"], reopened["player"]["position"])
    ):
        raise RuntimeError("The reopened world did not retain the saved player state")
    report = {
        "schemaVersion": 1,
        "client": file_info(executable),
        "preview": file_info(preview),
        "modsPage": mods,
        "firstState": first,
        "worldsOnReopen": worlds,
        "reopenedState": reopened,
    }
    if standalone:
        marker = "Prepared Horizon native-client resources"
        cold = marker in (qa / f"client-{first_index}.log").read_text(encoding="utf-8", errors="replace")
        warm = marker not in (qa / f"client-{second_index}.log").read_text(encoding="utf-8", errors="replace")
        if not warm or file_info(cache_receipt) != first_receipt:
            raise RuntimeError("The second standalone launch did not reuse the prepared resources")
        if expect_cold and not cold:
            raise RuntimeError("The first standalone launch did not exercise cold preparation")
        if expected_title and any((standalone[3] / name).exists() for name in ("preparation", "process-temp")):
            raise RuntimeError("The standalone bootstrap left a temporary work directory")
        if not (qa / "game/logs/latest.log").is_file():
            raise RuntimeError("The native client did not keep its log in the chosen game folder")
        report["bootstrap"] = file_info(standalone[0])
        report["cacheEntries"] = sorted(entry.name for entry in standalone[3].iterdir())
        report["cacheReuse"] = {"coldPrepared": cold, "secondLaunchReused": True, "receipt": first_receipt}
    if expected_title is not None:
        report["windowTitle"] = expected_title
    if check_pause:
        # The production bootstrap sanitizes HORIZON_* settings. Exercise the
        # exact same client directly so this test can restrict LAN to loopback.
        def check_reopened_pause(control):
            control("open", WORLD)
            control("wait-for", "loaded", "--timeout", "90")
            result = check_pause_menu(control)
            if (result["before"], result["after"]) != ("Открыть для сети", "Жалобы"):
                raise RuntimeError("The Russian pause menu labels differ")
            return result

        set_test_language(qa / "game", "ru_ru")
        prepared = standalone[3] / "resources" if standalone else resources
        report["pauseMenu"] = run_client(
            executable, controller, prepared, qa, second_index + 1, check_reopened_pause,
        )
    (qa / f"smoke{suffix}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def photograph(control, preview):
    control("wait-for", "loaded", "--timeout", "90")
    control("command", "gamemode spectator")
    control("command", "time set noon")
    control("command", "weather clear")
    control("tp", "26.5", "94", "-8.5")
    control("look", "-20", "29")
    control("key", "f1", "tap")
    control("wait-for", "loaded", "--timeout", "90")
    control("screenshot", str(preview))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, type=pathlib.Path)
    parser.add_argument("--controller", required=True, type=pathlib.Path)
    parser.add_argument("--resources", type=pathlib.Path)
    parser.add_argument("--qa", required=True, type=pathlib.Path)
    parser.add_argument("--refresh-preview", action="store_true")
    parser.add_argument("--reuse-world", action="store_true")
    parser.add_argument("--bootstrap", type=pathlib.Path)
    parser.add_argument("--minecraft", type=pathlib.Path)
    parser.add_argument("--java", type=pathlib.Path)
    parser.add_argument("--cache", type=pathlib.Path)
    parser.add_argument("--expect-title")
    parser.add_argument("--expect-cold", action="store_true")
    parser.add_argument("--check-pause-menu", action="store_true")
    args = parser.parse_args()
    standalone_paths = (args.bootstrap, args.minecraft, args.java, args.cache)
    if any(standalone_paths) and not all(standalone_paths):
        parser.error("Standalone smoke requires bootstrap, Minecraft, Java and cache paths")
    if bool(args.bootstrap) == bool(args.resources):
        parser.error("Supply either prepared resources or the standalone bootstrap")
    if (args.expect_title or args.expect_cold) and not args.bootstrap:
        parser.error("Window title or cold preparation verification requires the standalone bootstrap")
    if args.refresh_preview and args.reuse_world:
        parser.error("Choose either a new smoke run with the existing world or a preview refresh")
    standalone = tuple(path.resolve() for path in standalone_paths) if args.bootstrap else None
    qa = args.qa.resolve()
    try:
        executable = args.client.resolve(strict=True)
        controller = args.controller.resolve(strict=True)
        resources = args.resources.resolve(strict=True) if args.resources else None
        if args.refresh_preview:
            report = json.loads((qa / "smoke.json").read_text(encoding="utf-8"))
            if report["client"] != file_info(executable):
                raise ValueError("The previous smoke evidence belongs to a different client")

            def take_preview(control):
                control("open", WORLD)
                photograph(control, qa / "scenic-preview.png")

            run_client(executable, controller, resources, qa, 3, take_preview, standalone, args.expect_title)
        else:
            qa.mkdir(exist_ok=args.reuse_world)
            smoke(executable, controller, resources, qa, standalone, args.expect_title,
                  args.expect_cold, args.reuse_world, args.check_pause_menu)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
