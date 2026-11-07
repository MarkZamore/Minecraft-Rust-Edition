"""Check one-click sharing and actual guest chat commands in two Windows clients.

Loopback exercises Horizon's real integrated server and command permissions.
It does not replace two-account Steam invitation/relay acceptance.
"""

import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import time

from smoke_runtime import check_pause_menu, set_test_language
from stage_runtime import file_info


@contextlib.contextmanager
def client(executable, controller, resources, root, name):
    root.mkdir()
    game = root / "game"
    set_test_language(game, "en_us")
    endpoint = root / "control.json"
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("LANMINECRAFT_")}
    environment.update(HORIZON_NO_DISCORD="1", HORIZON_TEST_LOOPBACK="1",
                       HORIZON_HOME=str(root / "home"), HORIZON_NO_AUDIO="1")
    for key in ("HORIZON_LAUNCHER_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        environment[key] = str(root / key.lower())
    environment["HORIZON_LAUNCHER_NO_SELF_UPDATE"] = "1"
    with (root / "client.log").open("wb") as log:
        process = subprocess.Popen(
            [str(executable), "--resources", str(resources), "--home", str(root / "home"),
             "--game-dir", str(game), "--control-socket", str(endpoint), "--name", name],
            cwd=game, env=environment, stdout=log, stderr=log,
        )

        def control(*words, timeout=120):
            response = subprocess.run(
                [str(controller), "--socket", str(endpoint), *words], cwd=game,
                capture_output=True, encoding="utf-8", check=True, timeout=timeout,
            )
            reply = json.loads(response.stdout)
            if isinstance(reply, dict) and "error" in reply:
                raise RuntimeError(reply["error"])
            return reply

        try:
            deadline = time.monotonic() + 120
            while not endpoint.is_file():
                if process.poll() is not None:
                    raise RuntimeError(f"{name} exited with {process.returncode}")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{name} did not create its control socket")
                time.sleep(0.1)
            control("wait-for", "screen", "title", "--timeout", "90")
            if control("narration")["status"] != "off":
                raise RuntimeError("The first screen enabled narration")
            yield control
            control("quit", timeout=20)
            if process.wait(timeout=45) != 0:
                raise RuntimeError(f"{name} exited with {process.returncode}")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)


def wait_mode(control, expected):
    deadline = time.monotonic() + 20
    while (state := control("state"))["gamemode"] != expected:
        if time.monotonic() > deadline:
            raise RuntimeError(f"Expected gamemode {expected}, got {state!r}; "
                               f"chat={control('chat-log', '8')!r}")
        time.sleep(0.1)
    return state


def type_chat(control, text):
    control("key", "t", "tap")
    control("wait-for", "screen", "chat", "--timeout", "5")
    control("type", text)
    control("key", "enter", "tap")
    control("wait-for", "screen", "playing", "--timeout", "5")


def check_pair(executable, controller, resources, output, cheats):
    folder = output / ("commands-allowed" if cheats else "commands-denied")
    folder.mkdir()
    with client(executable, controller, resources, folder / "host", "EditionHostQA") as host:
        world = ["GuestCommandsQA", "137248"] + (["creative"] if cheats else [])
        host("open", *world)
        host("wait-for", "loaded", "--timeout", "120")
        pause = check_pause_menu(host)
        port = host("lan", "status")["port"]
        with client(executable, controller, resources, folder / "guest", "EditionGuestQA") as guest:
            guest("connect", f"127.0.0.1:{port}", "EditionGuestQA")
            guest("wait-for", "loaded", "--timeout", "120")
            if cheats:
                type_chat(guest, "/gamemode survival")
                wait_mode(guest, "survival")
                type_chat(guest, "/gamemode creative")
                state = wait_mode(guest, "creative")
            else:
                wait_mode(guest, "survival")
                type_chat(guest, "/gamemode creative")
                deadline = time.monotonic() + 15
                while True:
                    messages = guest("chat-log", "8")
                    if any("Unknown" in str(line) or "Incorrect" in str(line)
                           or "permission" in str(line).lower()
                           for line in messages["chat"]):
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"The denied command did not produce expected feedback: {messages!r}")
                    time.sleep(0.1)
                time.sleep(0.5)
                state = guest("state")
                if state["gamemode"] != "survival":
                    raise RuntimeError("Guest executed a command in a world without commands")
            return {"result": "PASS", "hostAllowsCommands": cheats,
                    "pauseMenu": pause, "guestFinalMode": state["gamemode"],
                    "guestChat": guest("chat-log", "8")["chat"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, type=Path)
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    executable, controller, resources, output = (
        value.resolve() for value in (args.client, args.controller, args.resources, args.out)
    )
    output.mkdir()
    from release_acceptance import execution_context
    acceptance_context = execution_context(executable.parent.parent)
    report = {"schemaVersion": 1, "client": file_info(executable),
              "acceptanceContext": acceptance_context,
              "transport": "Windows loopback; real Horizon server; guest T/type/Enter input",
              "twoAccountSteam": "NOT RUN", "status": "RUNNING"}
    try:
        report["commandsAllowed"] = check_pair(executable, controller, resources, output, True)
        report["commandsDenied"] = check_pair(executable, controller, resources, output, False)
        if execution_context(executable.parent.parent) != acceptance_context:
            raise RuntimeError("The packaged payload changed during multiplayer QA")
        report["status"] = "PASS"
    except Exception as error:
        report.update(status="FAIL", error=str(error))
        raise
    finally:
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )


if __name__ == "__main__":
    main()
