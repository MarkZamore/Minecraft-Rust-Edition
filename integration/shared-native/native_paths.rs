//! Immutable selection of the installed native payload for this process.
use std::{
    ffi::OsStr,
    fs,
    path::{Path, PathBuf},
    sync::OnceLock,
};

pub const PACK_ROOT_ENV: &str = "LANMINECRAFT_NATIVE_PACK_ROOT";

fn ordinary_directory(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let linked = metadata.file_type().is_symlink();
    #[cfg(windows)]
    let linked = {
        use std::os::windows::fs::MetadataExt;
        linked || metadata.file_attributes() & 0x400 != 0
    };
    if metadata.is_dir() && !linked {
        Ok(())
    } else {
        Err(format!("{}: expected an ordinary directory", path.display()))
    }
}

fn resolve(value: Option<&OsStr>) -> Result<Option<PathBuf>, String> {
    let Some(value) = value else {
        return Ok(None);
    };
    let pack = Path::new(value);
    if !pack.is_absolute() {
        return Err(format!("{PACK_ROOT_ENV}: expected an absolute pack directory"));
    }
    ordinary_directory(pack)?;
    let root = pack.join("native");
    ordinary_directory(&root)?;
    Ok(Some(root))
}

/// The bootstrap validates the managed profile. Resolve its path before loading
/// native code and use one directory for the lifetime of the process.
pub fn pack_native_root() -> Result<Option<&'static Path>, String> {
    static ROOT: OnceLock<Result<Option<PathBuf>, String>> = OnceLock::new();
    ROOT.get_or_init(|| resolve(std::env::var_os(PACK_ROOT_ENV).as_deref()))
        .as_ref()
        .map(|root| root.as_deref())
        .map_err(Clone::clone)
}

fn select(executable: &Path, scoped: Option<&Path>) -> PathBuf {
    scoped
        .or_else(|| executable.parent())
        .unwrap_or_else(|| Path::new(""))
        .to_owned()
}

pub fn native_root(executable: &Path) -> Result<PathBuf, String> {
    Ok(select(executable, pack_native_root()?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unscoped_launch_uses_the_original_sibling_paths() {
        let exe = Path::new("runtime").join("horizon-client.exe");
        assert_eq!(resolve(None).unwrap(), None);
        assert_eq!(select(&exe, None).join("native-mods.json"), exe.with_file_name("native-mods.json"));
        assert_eq!(select(&exe, None).join("horizon-native-mods"), exe.with_file_name("horizon-native-mods"));
    }

    #[test]
    fn explicit_scope_wins_for_a_shared_executable() {
        let exe = Path::new("runtime").join("horizon-client.exe");
        let edition = Path::new("Edition").join("native");
        let mods = Path::new("Mods").join("native");
        assert_eq!(select(&exe, Some(&edition)), edition);
        assert_eq!(select(&exe, Some(&mods)), mods);
    }

    #[test]
    fn invalid_explicit_scope_never_falls_back() {
        assert!(resolve(Some(OsStr::new(""))).is_err());
        assert!(resolve(Some(OsStr::new("relative-pack"))).is_err());
        let missing = std::env::current_exe().unwrap().join("no-such-pack");
        assert!(resolve(Some(missing.as_os_str())).is_err());
    }
}
