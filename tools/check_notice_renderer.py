"""Type-check the notice backend against the exact built WGPU and upload APIs.

This emits Rust metadata only. It neither builds the game nor creates a GPU,
window, notices session or user profile. Use an output in a registered task run.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--deps", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    upload = (args.upstream / "crates/horizon-client-render/src/gfx/upload.rs").resolve(strict=True)
    backend = (Path(__file__).resolve().parents[1] / "integration/steam/lanmc_notices.rs").resolve(strict=True)
    deps = args.deps.resolve(strict=True)
    output = args.out.resolve()
    output.mkdir()
    harness = output / "notice_renderer_check.rs"
    metadata = output / "notice_renderer_check.rmeta"
    # #[path] uses exact source files, including the real staging upload API.
    harness.write_text(
        "#![allow(dead_code)]\nmod gfx {\n"
        + f"#[path = {json.dumps(upload.as_posix())}] pub mod upload;\n"
        + f"#[path = {json.dumps(backend.as_posix())}] mod notices;\n}}\n",
        encoding="utf-8",
    )
    arguments = ["rustc", "--edition=2024", "--crate-type=lib", "--emit=metadata",
                 "-L", f"dependency={deps}", str(harness), "-o", str(metadata)]
    selected = {}
    for name in ("wgpu", "bytemuck"):
        libraries = list(deps.glob(f"lib{name}-*.rlib"))
        if not libraries:
            raise ValueError("Missing exact built dependency: " + name)
        library = max(libraries, key=lambda entry: entry.stat().st_mtime_ns)
        with library.open("rb") as stream:
            selected[name] = {"path": str(library), "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
        arguments.extend(["--extern", f"{name}={library}"])
    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8")
    (output / "compiler.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    report = {"status": "PASS" if result.returncode == 0 else "FAIL",
              "scope": "Rust metadata/type check; no game/GPU launch",
              "source": str(backend), "upstreamUpload": str(upload), "libraries": selected}
    report["sources"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (backend, *(backend.with_name(name) for name in (
            "lanmc_notice_protocol.rs", "lanmc_notice_state.rs", "lanmc_notice_raster.rs",
            "lanmc_notice_render.rs", "lanmc_notice_layout.rs")), upload)
    }
    report["rustc"] = subprocess.check_output(["rustc", "--version"], text=True).strip()
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if result.returncode:
        raise SystemExit(result.stderr)
    print("Notice backend type check PASS; no game or GPU was launched")


if __name__ == "__main__":
    main()
