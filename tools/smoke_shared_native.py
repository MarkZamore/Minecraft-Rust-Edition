"""Prove two Windows pack profiles use one verified native runtime through its real bootstrap.

Each --profile is a JSON document with id, packRoot, title, world, seed,
codeMods and contentMods. Both mod maps contain the exact loaded ID/version
pairs expected in the new local world's saved runtime inventory.
"""

import argparse
import ctypes
from ctypes import wintypes
import gzip
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time

from native_notices_qa import Session, process_ancestry, registered_output, remove_fixture
from smoke_startup import MARKERS, owned_root, snapshot
from stage_runtime import file_info


CLIENT_PATH = "native/horizon-client.exe"
SHA = re.compile(r"[0-9a-f]{64}")
PROFILE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,48}")
MOD_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}")
CACHE_MARKER = re.compile(MARKERS["cacheVerified"])


def require(value, message):
    if not value:
        raise ValueError(message)


class LevelNBT:
    """Read only the bounded NBT written by an owned QA world."""

    SCALARS = {1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}
    ARRAYS = {7: 1, 11: 4, 12: 8}

    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def read(self, count):
        data = self.stream.read(count)
        require(len(data) == count, "Truncated level.dat NBT")
        return data

    def number(self, fmt):
        return struct.unpack(">" + fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self):
        return self.read(self.number("H")).decode("utf-8", errors="replace")

    def payload(self, kind, depth):
        require(depth < 64, "Unexpected level.dat nesting depth")
        if kind in self.SCALARS:
            return self.number(self.SCALARS[kind])
        if kind in self.ARRAYS:
            count = self.number("i")
            require(0 <= count <= 2**20, "Unexpected level.dat array size")
            self.read(count * self.ARRAYS[kind])
            return None
        if kind == 8:
            return self.string()
        if kind == 9:
            item, count = self.number("B"), self.number("i")
            require(0 <= count <= 2**20 and (item != 0 or count == 0), "Invalid level.dat list")
            return [self.payload(item, depth + 1) for _ in range(count)]
        if kind == 10:
            result = {}
            while entry := self.number("B"):
                name = self.string()
                require(name not in result, f"Duplicate level.dat tag: {name}")
                result[name] = self.payload(entry, depth + 1)
            return result
        raise ValueError(f"Unsupported level.dat NBT type: {kind}")

    def root(self):
        require(self.number("B") == 10, "Expected a level.dat root compound")
        self.string()
        return self.payload(10, 0)


def read_world_data(path):
    with gzip.open(path, "rb") as file:
        data = file.read((16 << 20) + 1)
    require(len(data) <= (16 << 20), "Technical world metadata is unexpectedly large")
    return LevelNBT(data).root()


def level_evidence(path):
    level = read_world_data(path)
    inventory_written = all(key in level.get(container, {}) for container, key in
                            (("fml", "LoadingModList"), ("horizon", "ContentModList")))

    def inventory(container, key):
        compounds = level.get(container, {}).get(key, [])
        require(isinstance(compounds, list), "Invalid saved mod inventory")
        pairs = {row["ModId"]: row["ModVersion"] for row in compounds}
        require(len(pairs) == len(compounds), "Saved mod inventory has duplicate IDs")
        return pairs

    world = level["Data"]
    settings = path.parent / "data/minecraft/world_gen_settings.dat"
    seed = read_world_data(settings)["data"]["seed"] if settings.is_file() else world["WorldGenSettings"]["seed"]
    return {"file": file_info(path), "world": world["LevelName"], "seed": seed, "modInventoryWritten": inventory_written,
            "codeMods": inventory("fml", "LoadingModList"), "contentMods": inventory("horizon", "ContentModList")}


def paths_from_command_line(line):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    split = shell.CommandLineToArgvW
    split.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    values = split(line, ctypes.byref(count))
    if not values:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [values[index] for index in range(count.value)]
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = (ctypes.c_void_p,)
        free.restype = ctypes.c_void_p
        free(ctypes.cast(values, ctypes.c_void_p))


def actual_child(process, client):
    script = (
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        f"$rows=@(Get-CimInstance Win32_Process -Filter 'ParentProcessId={process.pid:d}' | "
        "Where-Object Name -eq 'horizon-client.exe' | Select-Object ProcessId,ExecutablePath,CommandLine); "
        "ConvertTo-Json -InputObject $rows -Compress"
    )
    response = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, encoding="utf-8", timeout=20, check=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    observed = json.loads(response.stdout)
    require(process.poll() is None and len(observed) == 1, "Expected one live owned Horizon bootstrap child")
    row = observed[0]
    require(Path(row["ExecutablePath"]).resolve(strict=True) == client, "The game used a different native client image")
    return row["ProcessId"], paths_from_command_line(row["CommandLine"])


def used_path(arguments, option):
    require(arguments.count(option) == 1, f"The actual native client requires exactly one {option} argument")
    return Path(arguments[arguments.index(option) + 1]).resolve(strict=True)


def resource_snapshot(resources, prepared):
    generation = resources.parent
    require(resources.name == "resources" and generation.parent == prepared and SHA.fullmatch(generation.name),
            "Child process did not use a common preparation generation")
    receipt = generation / "resources.json"
    raw = json.loads(receipt.read_bytes())
    require(raw.get("key") == generation.name and isinstance(raw.get("files"), dict) and raw["files"],
            "The generated shared-resource receipt does not identify its actual resource root")
    result = {"key": generation.name, "resources": str(resources), "files": len(raw["files"]),
              "receipt": snapshot(receipt)}
    archive = resources / "horizon-data.bin"
    if archive.is_file():
        result["dataArchive"] = snapshot(archive)
    return result


class SharedSession(Session):
    """Reuse owned Windows process, controller and termination helpers without notice fixtures."""

    def __init__(self, profile, runtime, bootstrap, args, out):
        super().__init__(profile["packRoot"], args.controller, out / "session", out, font_dir=None)
        self.profile, self.args = profile, args
        self.bootstrap = bootstrap
        self.client = runtime / CLIENT_PATH
        self.log_path = out / "client.log"
        self.resources = None

    def start(self):
        self.game.mkdir(parents=True)
        (self.game / "options.txt").write_text("narrator:0\nfullscreen:false\nlang:en_us\n", encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("LANMINECRAFT_", "HORIZON_", "STEAM")) and key.upper() != "JAVA_HOME"}
        env.update(LANMINECRAFT_NATIVE_PACK_ROOT=str(self.candidate),
                   LANMINECRAFT_NATIVE_PREPARED_CACHE=str(self.args.prepared_cache),
                   LANMINECRAFT_NATIVE_CACHE=str(self.cache),
                   LANMINECRAFT_NATIVE_VARIANT=self.profile["nativeVariant"],
                   APPDATA=str(self.qa / "empty-profile"), HORIZON_NO_DISCORD="1", RUST_LOG="info")
        arguments = [str(self.bootstrap), "--game-dir", str(self.game), "--control-socket", str(self.endpoint)]
        for label, path in (("--minecraft-dir", self.args.minecraft_dir), ("--java-home", self.args.java_home),
                            ("--server-jar", self.args.server_jar)):
            if path is not None:
                arguments.extend((label, str(path)))
        self.log = self.log_path.open("wb")
        self.process = subprocess.Popen(arguments, cwd=self.game, env=env, stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + self.args.startup_timeout
        while not self.endpoint.is_file():
            self.alive()
            if time.monotonic() >= deadline:
                raise TimeoutError("Shared bootstrap did not expose the control endpoint")
            time.sleep(0.25)
        self.control("wait-for", "screen", "title", "--timeout", "90")

    def launch_evidence(self, warm):
        pid, invocation = actual_child(self.process, self.client)
        ancestry = process_ancestry(pid, self.process.pid, self.client)
        require(file_info(self.client)["sha256"] == self.args.client_sha256,
                "The active native client image changed from its approved SHA-256")
        self.renderer_pid = pid
        title = self.window.inspect(pid)["title"]
        require(title == self.profile["title"], f"Wrong actual Window title: {title!r}")
        require(used_path(invocation, "--game-dir") == self.game and used_path(invocation, "--home") == self.game,
                "The child did not use its private game directory and home")
        self.resources = used_path(invocation, "--resources")
        receipt = resource_snapshot(self.resources, self.args.prepared_cache)
        require((self.cache / "session.lock").is_file() and all((self.cache / name).is_dir()
                    for name in ("process-home", "process-temp")),
                "The common bootstrap did not create a private session lease and process directories")
        require(not any((self.cache / name).exists() for name in ("resources", "resources.json")),
                "The profile also prepared its own redundant resource cache")
        events = CACHE_MARKER.findall(self.log_path.read_text(encoding="utf-8", errors="replace"))
        require(not warm or (len(events) == 1 and int(events[0][0]) == receipt["files"]),
                "The later pack did not verify and reuse the already prepared resource cache")
        renderer = self.control("status")["renderer"]
        require(renderer and renderer.get("backend"), "The launched client did not have a Windows renderer")
        return {"ancestry": ancestry, "windowTitle": title, "gameDirectory": str(self.game),
                "privateCache": str(self.cache), "verifiedCacheLog": events, "preparation": receipt,
                "renderer": {key: renderer[key] for key in ("backend", "gpu", "size")}}

    def exercise_world(self):
        self.control("open", self.profile["world"], self.profile["seed"], "creative")
        self.control("wait-for", "loaded", "--timeout", "90")
        state = self.control("state")
        require(state["gamemode"] == "creative" and state["dimension"] == "minecraft:overworld",
                "The profile did not load its fresh creative technical world")
        self.control("release")
        self.control("pause")
        self.control("save")

    def cleanup(self):
        # Horizon's generated mod content may refer to verified shared resources.
        # Only those recognized junctions can be unlinked before tree cleanup.
        links = []
        if self.game.exists():
            require(self.game.resolve() == self.game and not self.game.is_junction(), "QA game directory was redirected")
            for current, dirs, files in os.walk(self.game, followlinks=False):
                for name in [*dirs, *files]:
                    path = Path(current) / name
                    require(not path.is_symlink(), f"Unexpected link in owned game directory: {path}")
                    if path.is_junction():
                        parts = path.relative_to(self.game).parts
                        require(name in dirs and self.resources is not None and len(parts) >= 4
                                and parts[:2] == (".horizon-cache", "mod-content")
                                and re.fullmatch(r"[0-9a-f]{16}", parts[2])
                                and path.resolve(strict=True) == self.resources.joinpath(*parts[3:]),
                                f"Unexpected junction in owned game directory: {path}")
                        dirs.remove(name)
                        links.append(path)
        for link in links:
            link.rmdir()
        for path in (self.game, self.cache, self.qa / "empty-profile"):
            remove_fixture(path, self.qa)
        return not any(path.exists() for path in (self.game, self.cache, self.qa / "empty-profile"))


def profile_document(path):
    spec = json.loads(path.read_bytes())
    for label in ("id", "world"):
        require(isinstance(spec[label], str) and PROFILE_ID.fullmatch(spec[label]), f"Invalid profile {label}")
    require(isinstance(spec["title"], str) and spec["title"].strip(), "Invalid expected pack title")
    require(type(spec["seed"]) is int and -(2**63) <= spec["seed"] < 2**63, "Invalid technical world seed")
    for label in ("codeMods", "contentMods"):
        require(isinstance(spec[label], dict) and all(MOD_ID.fullmatch(key) and isinstance(value, str) and value
                                                      for key, value in spec[label].items()), f"Invalid profile {label}")
    pack = Path(spec["packRoot"])
    spec["packRoot"] = (pack if pack.is_absolute() else path.parent / pack).resolve(strict=True)
    return spec


def verify_inputs(args, profiles, launcher_tools):
    sys.path.insert(0, str(launcher_tools))
    try:
        import native_runtime_release as release
    finally:
        sys.path.pop(0)
    common = None
    common_files = None
    bootstrap = None
    for profile in profiles:
        pack = profile["packRoot"]
        descriptor_path = release.checked_file(pack, release.DESCRIPTOR)
        descriptor = release.read(descriptor_path)
        spec = descriptor["nativeRuntime"]["sharedRuntime"]
        require(spec["archive"]["sha256"] == args.runtime_sha256 == args.runtime.name,
                "Profile did not pin this exact immutable shared runtime directory")
        if common is None:
            common = spec
            common_files = release.validate_runtime(spec)
            for path, record in common_files.items():
                release.checked_file(args.runtime, path, record)
            family = release.read(release.checked_file(args.runtime, release.FAMILY, common_files[release.FAMILY]))
            minecraft = release.validate_family(family, common)
            bootstrap = release.checked_file(args.runtime, descriptor["nativeRuntime"]["executable"])
        require(common == spec and release.bind_descriptor(descriptor, common, common_files, minecraft) == descriptor
                and args.runtime / descriptor["nativeRuntime"]["executable"] == bootstrap,
                "Profile descriptor differs from the shared runtime contract or bootstrap")
        require(release.read(release.checked_file(pack, "native/presentation.json"))["name"] == profile["title"],
                "Profile presentation title differs from the expected title")
        require(all(not (pack / path).exists() for path in common_files), "Pack overlay still contains shared runtime files")
        registry_path = release.checked_file(pack, "native/native-mods.json")
        registry = release.read(registry_path)
        require(registry["clientSha256"] == args.client_sha256 and isinstance(registry["libraries"], list),
                "Native profile registry was built for a different client")
        for library in registry["libraries"]:
            name = library["file"]
            require(Path(name).name == name and Path(name).suffix.lower() == ".dll",
                    "Expected a profile-local native library")
            require(file_info(release.checked_file(pack, f"native/{name}"))["sha256"] == library["sha256"],
                    f"Profile native library differs from its registered SHA-256: {name}")
        profile.update(nativeVariant=descriptor["nativeRuntime"]["version"], descriptor=file_info(descriptor_path),
                       nativeRegistry=file_info(registry_path))
    require(file_info(bootstrap)["sha256"] == args.bootstrap_sha256
            and file_info(args.runtime / CLIENT_PATH)["sha256"] == args.client_sha256,
            "The exact common bootstrap or client SHA-256 differs")
    return bootstrap, {"id": common["id"], "version": common["version"], "archiveSha256": args.runtime_sha256,
                       "bootstrap": file_info(bootstrap), "client": file_info(args.runtime / CLIENT_PATH)}


def run_profile(profile, runtime, bootstrap, args, out, warm, evidence):
    session = SharedSession(profile, runtime, bootstrap, args, out)
    complete = False
    try:
        session.start()
        evidence.update(session.launch_evidence(warm))
        session.exercise_world()
        session.stop(success=True)
        world = level_evidence(session.game / "saves" / profile["world"] / "level.dat")
        evidence["savedWorld"] = world
        require(world["modInventoryWritten"], "Fresh shared-client world did not record the actual mod inventories")
        for label in ("world", "seed", "codeMods", "contentMods"):
            require(world[label] == profile[label], f"Wrong saved {label} for {profile['id']}: {world[label]!r}")
        complete = True
    finally:
        try:
            if not complete and session.process is not None:
                session.stop(success=False)
        finally:
            evidence["disposableGameAndCacheRemoved"] = session.cleanup()
    require(evidence["disposableGameAndCacheRemoved"], "The owned game/cache fixture remains after cleanup")


def run(args):
    require(os.name == "nt", "This smoke requires the Windows native clients")
    for field in ("runtime_sha256", "bootstrap_sha256", "client_sha256"):
        require(SHA.fullmatch(getattr(args, field)), f"Expected a lowercase SHA-256 for {field}")
    for field in ("runtime", "controller", "minecraft_dir", "java_home", "server_jar"):
        path = getattr(args, field)
        if path is not None:
            setattr(args, field, path.resolve(strict=True))
    qa = registered_output(args.qa)
    args.prepared_cache = Path(os.path.abspath(args.prepared_cache))
    task = owned_root(qa)
    registration = json.loads((task / "RUN.json").read_bytes())
    require(registration.get("status") == "active" and registration.get("owner"), "Use an active registered task")
    require(owned_root(args.prepared_cache) == task and not args.prepared_cache.is_relative_to(qa)
            and not qa.is_relative_to(args.prepared_cache), "Use a separate preparation cache in this registered task")
    profiles = [profile_document(path.resolve(strict=True)) for path in args.profile]
    require(all(owned_root(profile["packRoot"]) == task for profile in profiles),
            "Use isolated pack overlays in this registered task")
    require(len(profiles) >= 2 and all(len({profile[field] for profile in profiles}) == len(profiles)
                                      for field in ("id", "world", "title", "packRoot")),
            "Use two or more independently named pack overlays and technical worlds")
    require(len({tuple(sorted(set(profile["codeMods"]) | set(profile["contentMods"]))) for profile in profiles})
            == len(profiles), "Every profile must specify a distinct expected mod inventory")
    bootstrap, runtime = verify_inputs(args, profiles, task.parents[2] / "tools")
    require(len({profile["nativeVariant"] for profile in profiles}) == len(profiles), "The profiles share a Steam variant")
    identities = [{"id": profile["id"], "descriptor": profile["descriptor"], "nativeRegistry": profile["nativeRegistry"],
                   "nativeVariant": profile["nativeVariant"]} for profile in profiles]
    report = {"schemaVersion": 1, "status": "FAIL", "entryPoint": "real common Windows bootstrap",
              "runtime": runtime, "controller": file_info(args.controller), "profiles": identities}
    if args.verify_only:
        report.update(status="PASS", entryPoint="shared runtime file verification")
        return report
    qa.mkdir(parents=True)
    try:
        preparation = None
        for profile, row in zip(profiles, identities):
            run_profile(profile, args.runtime, bootstrap, args, qa / profile["id"], preparation is not None, row)
            current = row["preparation"]
            if preparation is None:
                preparation = current
            else:
                require(current == preparation, "A later profile rebuilt or used a different preparation receipt")
        report.update(status="PASS", sharedPreparation=preparation, reusedPreparationBy=len(profiles) - 1)
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (qa / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("runtime", "controller", "prepared-cache", "qa"):
        parser.add_argument(f"--{field}", required=True, type=Path)
    for field in ("runtime-sha256", "bootstrap-sha256", "client-sha256"):
        parser.add_argument(f"--{field}", required=True)
    parser.add_argument("--profile", action="append", type=Path, required=True)
    for field in ("minecraft-dir", "java-home", "server-jar"):
        parser.add_argument(f"--{field}", type=Path)
    parser.add_argument("--startup-timeout", type=int, default=1200)
    parser.add_argument("--verify-only", action="store_true", help="Check the prepared inputs without starting either client")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
    except (AssertionError, KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Shared runtime QA: {error}\n")


if __name__ == "__main__":
    main()
