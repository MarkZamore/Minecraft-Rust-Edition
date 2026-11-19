//! Fetch the exact Mojang inputs authenticated by Horizon's research lock.
//! Existing installations are optional, read-only sources for verified files.

use super::*;

#[derive(Deserialize)]
struct Artifact {
    url: String,
    sha1: String,
    size: u64,
}

struct InputFile {
    artifact: Artifact,
    target: PathBuf,
    source: Option<PathBuf>,
}

impl InputFile {
    fn obtain(&self) -> Result<()> {
        let Artifact { url, sha1, size } = &self.artifact;
        ensure!(
            *size > 0
                && *size <= 512 * 1024 * 1024
                && sha1.len() == 40
                && sha1.bytes().all(|c| c.is_ascii_hexdigit()),
            "Invalid Minecraft artifact metadata"
        );
        if verified_file::<Sha1>(&self.target, *size, sha1)? {
            return Ok(());
        }
        create_unlinked_dir(self.target.parent().context("Missing input directory")?)?;
        if let Some(source) = &self.source
            && verified_file::<Sha1>(source, *size, sha1)?
        {
            let partial = self.target.with_extension("part");
            ensure_unlinked(&partial)?;
            let result = (|| {
                fs::copy(source, &partial)?;
                ensure!(
                    verified_file::<Sha1>(&partial, *size, sha1)?,
                    "Minecraft source changed while copying"
                );
                if self.target.exists() {
                    fs::remove_file(&self.target)?;
                }
                fs::rename(&partial, &self.target)?;
                Ok(())
            })();
            if partial.exists() {
                fs::remove_file(partial)?;
            }
            return result;
        }
        download::<Sha1>(url, Some(*size), sha1, &self.target)?;
        Ok(())
    }
}

pub(super) fn acquire(
    pins: &Pins,
    cache: &Path,
    source: Option<MinecraftSource>,
) -> Result<Minecraft> {
    let root = child(cache, "minecraft")?;
    let version_dir = child(&root, &format!("versions/{}", pins.version))?;
    create_unlinked_dir(&version_dir)?;
    let manifest = child(&version_dir, &format!("{}.json", pins.version))?;
    let approved = source.as_ref().and_then(|source| {
        source
            .manifest_sha1
            .as_deref()
            .map(|sha1| (&source.files.manifest, sha1))
    });
    let expected = approved.map_or(pins.manifest_sha1.as_str(), |(_, sha1)| sha1);
    if !manifest.is_file() || hash::<Sha1>(&manifest)? != expected {
        if let Some((source_manifest, _)) = approved {
            fs::copy(source_manifest, &manifest)?;
            ensure!(
                hash::<Sha1>(&manifest)? == expected,
                "Minecraft manifest changed while copying"
            );
        } else {
            let url = format!(
                "https://piston-meta.mojang.com/v1/packages/{expected}/{}.json",
                pins.version
            );
            download::<Sha1>(&url, None, expected, &manifest)?;
        }
    }
    let version: Value = serde_json::from_reader(File::open(&manifest)?)?;
    ensure!(
        version["id"].as_str() == Some(&pins.version),
        "Unexpected Minecraft version"
    );
    let input = Minecraft {
        client: child(&version_dir, &format!("{}.jar", pins.version))?,
        manifest,
        libraries: child(&root, "libraries")?,
        assets: child(&root, "assets")?,
    };
    let client: Artifact = serde_json::from_value(version["downloads"]["client"].clone())?;
    ensure!(
        client.sha1 == pins.client_sha1
            && client.url
                == format!(
                    "https://piston-data.mojang.com/v1/objects/{}/client.jar",
                    pins.client_sha1
                ),
        "Minecraft client does not match Horizon's pin"
    );
    let source = source.as_ref().map(|source| &source.files);
    let mut files = vec![InputFile {
        artifact: client,
        target: input.client.clone(),
        source: source.map(|input| input.client.clone()),
    }];
    // These entries are authenticated by the pinned version JSON. Fetching the
    // listed artifacts also covers the preparer's platform-specific library selection.
    for library in version["libraries"]
        .as_array()
        .context("Missing Minecraft libraries")?
    {
        let entry = &library["downloads"]["artifact"];
        let relative = entry["path"]
            .as_str()
            .context("Missing Minecraft library path")?;
        let artifact: Artifact = serde_json::from_value(entry.clone())?;
        ensure!(
            artifact.url == format!("https://libraries.minecraft.net/{relative}"),
            "Unexpected library URL"
        );
        files.push(InputFile {
            artifact,
            target: child(&input.libraries, relative)?,
            source: source
                .map(|input| child(&input.libraries, relative))
                .transpose()?,
        });
    }
    let index = &version["assetIndex"];
    let id = index["id"]
        .as_str()
        .context("Missing Minecraft asset index ID")?;
    let relative = format!("indexes/{id}.json");
    let index_file = InputFile {
        artifact: serde_json::from_value(index.clone())?,
        target: child(&input.assets, &relative)?,
        source: source
            .map(|input| child(&input.assets, &relative))
            .transpose()?,
    };
    ensure!(
        index_file.artifact.url
            == format!(
                "https://piston-meta.mojang.com/v1/packages/{}/{id}.json",
                index_file.artifact.sha1
            ),
        "Unexpected Minecraft asset index URL"
    );
    index_file.obtain()?;
    let assets: Value = serde_json::from_reader(File::open(&index_file.target)?)?;
    let mut objects = BTreeMap::new();
    for entry in assets["objects"]
        .as_object()
        .context("Missing Minecraft asset objects")?
        .values()
    {
        let hash = entry["hash"]
            .as_str()
            .context("Missing Minecraft asset SHA-1")?;
        ensure!(
            hash.len() == 40 && hash.bytes().all(|c| c.is_ascii_hexdigit()),
            "Invalid asset SHA-1"
        );
        let size = entry["size"]
            .as_u64()
            .context("Missing Minecraft asset size")?;
        if let Some(previous) = objects.insert(hash, size) {
            ensure!(previous == size, "Conflicting Minecraft asset sizes");
        }
    }
    for (hash, size) in objects {
        let relative = format!("objects/{}/{hash}", &hash[..2]);
        files.push(InputFile {
            artifact: Artifact {
                url: format!(
                    "https://resources.download.minecraft.net/{}/{hash}",
                    &hash[..2]
                ),
                sha1: hash.into(),
                size,
            },
            target: child(&input.assets, &relative)?,
            source: source
                .map(|input| child(&input.assets, &relative))
                .transpose()?,
        });
    }
    eprintln!(
        "Checking {} pinned Minecraft {} input files",
        files.len(),
        pins.version
    );
    let workers = std::thread::available_parallelism()
        .map_or(1, usize::from)
        .min(8);
    std::thread::scope(|scope| -> Result<()> {
        let handles: Vec<_> = files
            .chunks(files.len().div_ceil(workers))
            .map(|chunk| {
                scope.spawn(move || -> Result<()> {
                    for file in chunk {
                        file.obtain().with_context(|| {
                            format!("Could not prepare {}", file.target.display())
                        })?;
                    }
                    Ok(())
                })
            })
            .collect();
        for handle in handles {
            handle
                .join()
                .map_err(|_| anyhow::anyhow!("Minecraft downloader stopped"))??;
        }
        Ok(())
    })?;
    Ok(input)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unrecognized_shared_manifest_can_still_donate_verified_inputs() -> Result<()> {
        let root = env::temp_dir().join(format!("lanmc-minecraft-donor-{}", std::process::id()));
        fs::create_dir(&root)?;
        let result = (|| {
            let cache = root.join("cache");
            let shared = root.join("shared");
            let client = b"pinned client";
            let library = b"pinned library";
            let object = b"pinned asset";
            let client_sha1 = format!("{:x}", Sha1::digest(client));
            let library_sha1 = format!("{:x}", Sha1::digest(library));
            let object_sha1 = format!("{:x}", Sha1::digest(object));
            let index = serde_json::to_vec(&serde_json::json!({
                "objects": { "asset": { "hash": object_sha1, "size": object.len() } }
            }))?;
            let index_sha1 = format!("{:x}", Sha1::digest(&index));
            let version = serde_json::to_vec(&serde_json::json!({
                "id": "26.3",
                "downloads": { "client": {
                    "url": format!("https://piston-data.mojang.com/v1/objects/{client_sha1}/client.jar"),
                    "sha1": client_sha1, "size": client.len()
                } },
                "libraries": [{ "downloads": { "artifact": {
                    "path": "fixture/library.jar",
                    "url": "https://libraries.minecraft.net/fixture/library.jar",
                    "sha1": library_sha1, "size": library.len()
                } } }],
                "assetIndex": {
                    "id": "34",
                    "url": format!("https://piston-meta.mojang.com/v1/packages/{index_sha1}/34.json"),
                    "sha1": index_sha1, "size": index.len()
                }
            }))?;
            let pins = Pins {
                version: "26.3".into(),
                manifest_sha1: format!("{:x}", Sha1::digest(&version)),
                accepted_manifest_sha1: Vec::new(),
                client_sha1,
                server_sha1: String::new(),
                java_major: 25,
            };
            let write_file = |path: &Path, bytes: &[u8]| -> Result<()> {
                fs::create_dir_all(path.parent().context("Missing fixture parent")?)?;
                fs::write(path, bytes)?;
                Ok(())
            };
            let manifest = "versions/26.3/26.3.json";
            let foreign_manifest = b"{\"id\":\"26.3\",\"metadata\":\"republished\"}";
            let object_path = format!("objects/{}/{object_sha1}", &object_sha1[..2]);
            write_file(&shared.join(manifest), foreign_manifest)?;
            write_file(&shared.join("versions/26.3/26.3.jar"), client)?;
            write_file(&shared.join("libraries/fixture/library.jar"), library)?;
            write_file(&shared.join("assets/indexes/34.json"), &index)?;
            write_file(&shared.join("assets").join(&object_path), object)?;
            write_file(&cache.join("minecraft").join(manifest), &version)?;

            let donor = select_minecraft_source(&pins, [shared.clone()], false)?
                .context("Expected optional donor despite the newer manifest")?;
            assert!(donor.manifest_sha1.is_none());
            let prepared = acquire(&pins, &cache, Some(donor))?;
            assert_eq!(fs::read(prepared.manifest)?, version);
            assert_eq!(fs::read(prepared.client)?, client);
            assert_eq!(
                fs::read(prepared.libraries.join("fixture/library.jar"))?,
                library
            );
            assert_eq!(fs::read(prepared.assets.join("indexes/34.json"))?, index);
            assert_eq!(fs::read(prepared.assets.join(object_path))?, object);
            assert_eq!(fs::read(shared.join(manifest))?, foreign_manifest);
            let rejected = select_minecraft_source(&pins, [shared], true)
                .err()
                .context("Explicit unverified manifests must still be rejected")?;
            assert!(rejected.to_string().contains("--minecraft-dir"));
            Ok(())
        })();
        fs::remove_dir_all(&root)?;
        result
    }

    #[test]
    fn repair_and_retry_preserve_sources_and_reject_unverified_downloads() -> Result<()> {
        use std::io::Write;
        use std::net::TcpListener;

        let root = env::temp_dir().join(format!("lanmc-input-repair-{}", std::process::id()));
        fs::create_dir(&root)?;
        let result = (|| {
            let source = root.join("source.jar");
            let target = root.join("input.jar");
            let valid = b"verified bytes";
            let corrupt = b"corrupt! bytes";
            fs::write(&source, valid)?;
            fs::write(&target, corrupt)?;
            let listener = TcpListener::bind("127.0.0.1:0")?;
            let address = listener.local_addr()?;
            let mut input = InputFile {
                artifact: Artifact {
                    url: format!("http://{address}/artifact"),
                    sha1: hash::<Sha1>(&source)?,
                    size: valid.len() as u64,
                },
                source: Some(source.clone()),
                target: target.clone(),
            };
            input.obtain()?;
            assert_eq!(fs::read(&target)?, valid);
            assert_eq!(fs::read(&source)?, valid);
            fs::write(&target, corrupt)?;
            fs::write(&source, corrupt)?;
            let server = std::thread::spawn(move || -> io::Result<()> {
                let (mut stream, _) = listener.accept()?;
                let mut request = [0; 4096];
                stream.read(&mut request)?;
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    corrupt.len()
                )?;
                stream.write_all(corrupt)
            });
            assert!(input.obtain().is_err());
            server.join().unwrap()?;
            assert_eq!(fs::read(&target)?, corrupt);
            assert!(!target.with_extension("part").exists());
            fs::write(&source, valid)?;
            input.obtain()?;
            input.source = None;
            input.obtain()?;
            assert_eq!(fs::read(&target)?, valid);
            assert_eq!(fs::read(&source)?, valid);
            assert!(child(&root, "../outside.jar").is_err());
            Ok(())
        })();
        fs::remove_dir_all(&root)?;
        result
    }
}
