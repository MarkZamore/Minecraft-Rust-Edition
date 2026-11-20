"""Prepare the exact f00125f SDK source for a shared executable and pack DLLs."""

import argparse
import hashlib
import json
from pathlib import Path


REVISION = "f00125f61f794aaa3d4e950d301f353b35b6ee3d"
RECIPE = Path(__file__).resolve().parent
RUNTIME = "crates/horizon-mod-package/src/runtime.rs"
PATHS = "crates/horizon-mod-package/src/lanminecraft_native_paths.rs"
SELECTION = "crates/horizon-mod-package/src/selection.rs"
ENTRY = "crates/horizon-client/src/entry.rs"
LOADING = "crates/horizon-mod-host/src/loading.rs"
LIBRARIES = "crates/horizon-mod-host/src/native_libraries.rs"
WASM = "crates/horizon-mod-wasm/src/lib.rs"
SOURCE_SHA256 = {
    ENTRY: "9aba46f6b3a62eb0698d0e5f6557dc415cbda671622324be3efbbd2b056f4c68",
    RUNTIME: "b6e2f04042cbcd71e27d29ad72635206197fa9dac7a55314d761e12570d62ca8",
    SELECTION: "e6ee19e113d198c6fdc168b46f04b095403bdabf340e3763273b78c2f02fb543",
    LOADING: "4ac333e80fa392882857e526b917ae95a31e775899808d935599e0fdcba9677d",
    LIBRARIES: "cc9d05484ee10c51f19de73ec3da6b75493810be5b97d90ca032702ee98b8204",
    WASM: "a799952a08cc365dd0d788d4aa2151d8f1d4c7c8cf9d154d53a512d4eef30a2e",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError(f"Shared native source anchor changed: {before!r}")
    return source.replace(before, after)


def changes(originals: dict[str, bytes]) -> dict[str, bytes]:
    files = {}
    for name, digest in SOURCE_SHA256.items():
        raw = originals[name]
        if sha(raw) != digest:
            raise ValueError(f"Shared native source differs from Horizon {REVISION}: {name}")
        files[name] = raw.decode("utf-8")

    runtime = replace(files[RUNTIME], "use mod_vocab::__private::native::BuiltMod;", """use mod_vocab::__private::native::BuiltMod;

#[path = "lanminecraft_native_paths.rs"]
pub mod native_paths;""")
    files[RUNTIME] = replace(runtime, """        let root = executable.with_file_name(NATIVE_DIR);
        let mut packages = Vec::with_capacity(built.len());""",
        """        Self::load_directory(executable.with_file_name(NATIVE_DIR), built)
    }

    pub fn load_from_native_root(root: &Path, built: &'static [BuiltMod]) -> Result<Self, RuntimeError> {
        Self::load_directory(root.join(NATIVE_DIR), built)
    }

    fn load_directory(root: PathBuf, built: &'static [BuiltMod]) -> Result<Self, RuntimeError> {
        let mut packages = Vec::with_capacity(built.len());""")
    files[LOADING] = replace(files[LOADING],
        "            horizon_mod_package::runtime::Runtime::load(exe, built).map_err(Into::into)",
        """            let root = horizon_mod_package::runtime::native_paths::native_root(exe)?;
            horizon_mod_package::runtime::Runtime::load_from_native_root(&root, built).map_err(Into::into)""")
    library = replace(files[LIBRARIES], "fn load(folder: &Path) -> Result",
                      "fn load(folder: &Path, scoped: bool) -> Result")
    library = replace(library, "struct Record {\n    libraries: Vec<Library>,",
        '''struct Record {
    #[serde(rename = "abiBuildId")]
    abi_build_id: Option<String>,
    libraries: Vec<Library>,''')
    library = replace(library, "Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),",
                      "Err(e) if e.kind() == std::io::ErrorKind::NotFound && !scoped => return Ok(Vec::new()),")
    library = replace(library, "    verify(folder, &record.libraries)?;",
        '''    if scoped && record.abi_build_id.as_deref() != Some(GAME_BUILD_ID) {
        return Err(format!("native-mods.json: expected shared ABI {GAME_BUILD_ID}"));
    }
    verify(folder, &record.libraries)?;''')
    files[LIBRARIES] = replace(library, '.and_then(|exe| load(exe.parent().expect("executable parent")));',
        '''.and_then(|exe| {
            use horizon_mod_package::runtime::native_paths;
            let scoped = native_paths::pack_native_root()?.is_some();
            load(&native_paths::native_root(&exe)?, scoped)
        });''')
    files[ENTRY] = replace(files[ENTRY], "        let launch = LaunchArguments::for_command(&command)?;",
        """        let mut launch = LaunchArguments::for_command(&command)?;
        if let Some(native) = horizon_mod_package::runtime::native_paths::pack_native_root()? {
            let mut packaged = horizon_mod_package::find(&native.join(horizon_mod_package::mod_list::DIR));
            packaged.retain(|path| {
                std::fs::symlink_metadata(path).is_ok_and(|entry| entry.is_file())
                    && !launch.context.dev_mods.contains(path)
            });
            packaged.append(&mut launch.context.dev_mods);
            launch.context.dev_mods = packaged;
        }""")
    files[SELECTION] = replace(files[SELECTION],
        """    paths.extend(dev.iter().cloned());
    paths.into_iter().filter_map(move |path| {""",
        """    let paths = if crate::runtime::native_paths::pack_native_root().is_ok_and(|root| root.is_some()) {
        dev.iter().cloned().chain(paths).collect()
    } else {
        paths.extend(dev.iter().cloned());
        paths
    };
    paths.into_iter().filter_map(move |path| {""")
    files[WASM] = replace(files[WASM],
        """            .chain(std::env::current_exe().ok().into_iter().flat_map(|exe| {
                horizon_mod_package::find(
                    &exe.with_file_name(horizon_mod_package::mod_list::NATIVE_DIR),
                )
            }))""",
        """            .chain(std::env::current_exe().ok().into_iter().flat_map(|exe| {
                horizon_mod_package::runtime::native_paths::native_root(&exe)
                    .into_iter()
                    .flat_map(|root| horizon_mod_package::find(
                        &root.join(horizon_mod_package::mod_list::NATIVE_DIR),
                    ))
            }))""")

    return {**{name: source.encode("utf-8") for name, source in files.items()},
            PATHS: (RECIPE / "native_paths.rs").read_bytes()}


def apply(source: Path) -> dict[str, str]:
    source = source.resolve(strict=True)
    files = {name: (source / name).read_bytes() for name in SOURCE_SHA256}
    patched = changes(files)
    new_file = source / PATHS
    if new_file.exists() or new_file.is_symlink() or new_file.is_junction():
        raise ValueError(f"Shared native destination already exists: {new_file}")
    for name, contents in patched.items():
        path = source / name
        if path.resolve() != path:
            raise ValueError(f"Shared native staged source uses a linked path: {path}")
    for name, contents in patched.items():
        (source / name).write_bytes(contents)
    return {name: sha(contents) for name, contents in patched.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps({"upstream": REVISION, "files": apply(args.source)}, indent=2))


if __name__ == "__main__":
    main()
