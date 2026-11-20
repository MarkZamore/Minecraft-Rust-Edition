//! A verified common runtime can launch a separate, pack-owned native overlay.
use crate::{child, create_unlinked_dir, ensure_unlinked, tree_files};
use anyhow::{Context, Result, bail, ensure};
use fs2::FileExt;
use serde::Deserialize;
use std::{
    fs::{self, File, OpenOptions},
    path::{Component, Path, PathBuf},
};

pub(super) const PACK_ROOT: &str = "LANMINECRAFT_NATIVE_PACK_ROOT";
pub(super) const PREPARED_CACHE: &str = "LANMINECRAFT_NATIVE_PREPARED_CACHE";

pub(super) struct Layout {
    pub(super) runtime_native: PathBuf,
    pub(super) pack_native: PathBuf,
    pub(super) pack: PathBuf,
    pub(super) shared_preparation: Option<PathBuf>,
}

#[derive(Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
struct Identity {
    schema_version: u32,
    id: String,
    version: String,
    platform: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Family {
    #[serde(flatten)]
    identity: Identity,
    minecraft_version: String,
}

#[derive(Deserialize)]
struct ArchivePin {
    sha256: String,
}

#[derive(Deserialize)]
struct SharedRuntime {
    #[serde(flatten)]
    identity: Identity,
    archive: ArchivePin,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct NativeRuntime {
    shared_runtime: SharedRuntime,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct PackDescriptor {
    schema_version: u32,
    minecraft_version: String,
    native_runtime: NativeRuntime,
}

impl Layout {
    pub(super) fn checked(
        executable: &Path,
        pack_root: Option<PathBuf>,
        prepared_cache: Option<PathBuf>,
    ) -> Result<Self> {
        let runtime_native = executable.parent().context("No installation directory")?;
        let runtime_root = runtime_native.parent().context("No runtime directory")?;
        let (pack, pack_native) = match (&pack_root, &prepared_cache) {
            (None, None) => (runtime_root.to_owned(), runtime_native.to_owned()),
            (Some(pack), Some(_)) => {
                disjoint(&resolved_location(pack)?, &resolved_location(runtime_root)?)?;
                let pack_native = child(pack, "native")?;
                let pack_descriptor: PackDescriptor =
                    serde_json::from_reader(File::open(child(pack, "portable-pack.json")?)?)
                        .context("Missing or invalid shared native pack descriptor")?;
                let family: Family = serde_json::from_reader(File::open(child(
                    runtime_native,
                    "runtime-family.json",
                )?)?)
                .context("Missing or invalid shared runtime identity")?;
                let declared = pack_descriptor.native_runtime.shared_runtime;
                let archive_sha = &declared.archive.sha256;
                ensure!(
                    pack_descriptor.schema_version == 2
                        && family.identity.schema_version == 1
                        && family.identity.platform == "windows-x64"
                        && family.identity == declared.identity
                        && !family.identity.id.is_empty()
                        && !family.identity.version.is_empty()
                        && !family.minecraft_version.is_empty()
                        && family.minecraft_version == pack_descriptor.minecraft_version
                        && is_sha256(archive_sha)
                        && runtime_root.file_name().and_then(|name| name.to_str())
                            == Some(archive_sha.as_str()),
                    "The pack descriptor does not match this shared native runtime"
                );
                // The shared client opens managed package data from this exact root.
                tree_files(&pack_native)?;
                (pack.clone(), pack_native)
            }
            _ => bail!("{PACK_ROOT} and {PREPARED_CACHE} must be supplied together"),
        };
        Ok(Self {
            runtime_native: runtime_native.to_owned(),
            pack_native,
            pack,
            shared_preparation: prepared_cache,
        })
    }

    pub(super) fn check_shared_preparation(&self, cache: &Path, game: &Path) -> Result<()> {
        let Some(store) = &self.shared_preparation else {
            return Ok(());
        };
        let store = resolved_location(store)?;
        let runtime = resolved_location(
            self.runtime_native
                .parent()
                .context("No runtime directory")?,
        )?;
        let pack = resolved_location(&self.pack)?;
        let cache = resolved_location(cache)?;
        let game = resolved_location(game)?;
        for other in [&runtime, &pack, &cache, &game] {
            disjoint(&store, other)?;
        }
        disjoint(&runtime, &cache)?;
        disjoint(&runtime, &game)?;
        Ok(())
    }
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn resolved_location(path: &Path) -> Result<PathBuf> {
    ensure!(
        path.is_absolute()
            && !path
                .components()
                .any(|component| component == Component::ParentDir),
        "Expected an absolute native runtime path: {}",
        path.display()
    );
    let mut nearest = None;
    for ancestor in path.ancestors() {
        if let Some(metadata) = ensure_unlinked(ancestor)? {
            ensure!(
                metadata.is_dir(),
                "Expected a native runtime directory: {}",
                ancestor.display()
            );
            nearest.get_or_insert(ancestor);
        }
    }
    let ancestor = nearest.context("Native runtime path has no existing ancestor")?;
    Ok(fs::canonicalize(ancestor)?.join(path.strip_prefix(ancestor)?))
}

fn overlaps(parent: &Path, child: &Path) -> bool {
    let mut child = child.components();
    parent.components().all(|part| {
        child
            .next()
            .is_some_and(|value| part.as_os_str().eq_ignore_ascii_case(value.as_os_str()))
    })
}

fn disjoint(first: &Path, second: &Path) -> Result<()> {
    ensure!(
        !overlaps(first, second) && !overlaps(second, first),
        "Native runtime locations overlap: {} and {}",
        first.display(),
        second.display()
    );
    Ok(())
}

fn open_lock(root: &Path, name: &str) -> Result<File> {
    Ok(OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(child(root, name)?)?)
}

/// Serialize initialization and repair. After checkout, each game keeps its
/// own shared lease until the bootstrap has finished waiting for that game.
pub(super) fn prepare(
    root: &Path,
    key: &str,
    load: impl FnOnce(&Path, bool) -> Result<PathBuf>,
) -> Result<(PathBuf, File)> {
    ensure!(is_sha256(key), "Invalid prepared resource identity");
    let cache = child(root, key)?;
    create_unlinked_dir(&cache)?;
    let gate = open_lock(&cache, "prepare.lock")?;
    FileExt::lock_exclusive(&gate)?;
    let lease = open_lock(&cache, "use.lock")?;
    let writable = match FileExt::try_lock_exclusive(&lease) {
        Ok(()) => true,
        Err(error) if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() => {
            FileExt::lock_shared(&lease)?;
            false
        }
        Err(error) => return Err(error.into()),
    };
    let resources = load(&cache, writable)?;
    if writable {
        FileExt::unlock(&lease)?;
        FileExt::lock_shared(&lease)?;
    }
    drop(gate);
    Ok((resources, lease))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(test: &str, work: impl FnOnce(&Path) -> Result<()>) -> Result<()> {
        let root = std::env::temp_dir().join(format!("lanmc-shared-{test}-{}", std::process::id()));
        fs::create_dir(&root)?;
        let result = work(&root);
        crate::remove_tree(&root)?;
        result
    }

    #[test]
    fn checked_layout_selects_the_matching_overlay_and_keeps_locations_disjoint() -> Result<()> {
        fixture("layout", |root| {
            let sha = "1a".repeat(32);
            let pack = root.join("Packs/Overlay");
            let pack_native = pack.join("native");
            let runtime = root.join("NativeRuntimes").join(&sha).join("native");
            let prepared = root.join("NativePrepared");
            fs::create_dir_all(&pack_native)?;
            fs::create_dir_all(&runtime)?;
            let family = serde_json::json!({
                "schemaVersion": 1, "id": "common", "version": "nightly-a",
                "platform": "windows-x64", "minecraftVersion": "26.3"
            });
            fs::write(
                runtime.join("runtime-family.json"),
                serde_json::to_vec(&family)?,
            )?;
            let mut descriptor = serde_json::json!({
                "schemaVersion": 2, "minecraftVersion": "26.3",
                "nativeRuntime": { "sharedRuntime": {
                    "schemaVersion": 1, "id": "common", "version": "nightly-a",
                    "platform": "windows-x64", "archive": { "sha256": sha }
                }}
            });
            let exe = runtime.join("launch-rust-edition.exe");
            let descriptor_path = pack.join("portable-pack.json");
            fs::write(&descriptor_path, serde_json::to_vec(&descriptor)?)?;
            let layout = Layout::checked(&exe, Some(pack.clone()), Some(prepared.clone()))?;
            assert_eq!(layout.runtime_native, runtime);
            assert_eq!(layout.pack_native, pack_native);
            assert_eq!(layout.pack, pack);
            let game = pack.join("game");
            layout.check_shared_preparation(&root.join("Runtimes/Overlay/Native"), &game)?;
            assert!(
                layout
                    .check_shared_preparation(&prepared.join("wrong"), &game)
                    .is_err()
            );
            assert!(
                layout
                    .check_shared_preparation(
                        &root.join("Runtimes/Overlay"),
                        &prepared.join("wrong")
                    )
                    .is_err()
            );
            assert!(Layout::checked(&exe, Some(pack.clone()), None).is_err());

            descriptor["nativeRuntime"]["sharedRuntime"]["archive"]["sha256"] =
                "ff".repeat(32).into();
            fs::write(&descriptor_path, serde_json::to_vec(&descriptor)?)?;
            assert!(Layout::checked(&exe, Some(pack.clone()), Some(prepared)).is_err());
            let legacy = Layout::checked(&pack_native.join("launch.exe"), None, None)?;
            assert_eq!(legacy.runtime_native, pack_native);
            assert_eq!(legacy.pack_native, legacy.runtime_native);
            assert!(legacy.shared_preparation.is_none());
            Ok(())
        })
    }

    #[test]
    fn shared_leases_prevent_repair_while_any_pack_reads() -> Result<()> {
        fixture("cache", |root| {
            let key = "ab".repeat(32);
            let (first, first_lease) = prepare(root, &key, |folder, writable| {
                assert!(writable);
                let file = folder.join("resources.json");
                fs::write(&file, b"shared prepared bytes")?;
                Ok(file)
            })?;
            let (second, second_lease) = prepare(root, &key, |folder, writable| {
                assert!(!writable);
                Ok(folder.join("resources.json"))
            })?;
            assert_eq!(first, second);
            assert_eq!(fs::read(&second)?, b"shared prepared bytes");
            drop(first_lease);
            let (_, third_lease) = prepare(root, &key, |folder, writable| {
                assert!(!writable);
                Ok(folder.join("resources.json"))
            })?;
            drop((second_lease, third_lease));
            let (_, _) = prepare(root, &key, |folder, writable| {
                assert!(writable);
                Ok(folder.join("resources.json"))
            })?;
            Ok(())
        })
    }
}
