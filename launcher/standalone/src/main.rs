#[cfg(not(all(
    target_os = "windows",
    target_arch = "x86_64",
    target_feature = "crt-static"
)))]
compile_error!("The portable Windows x64 bootstrap requires the static MSVC runtime.");
mod prepared_data;

use anyhow::{Context, Result, bail, ensure};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha1::Sha1;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    env,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{self, Read},
    os::windows::fs::MetadataExt,
    path::{Path, PathBuf},
    process::{Command, ExitCode},
    time::{Duration, Instant},
};

const REPARSE_POINT: u32 = 0x400;
const JAVA_URL: &str = "https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.4.1%2B1/OpenJDK25U-jdk_x64_windows_hotspot_25.0.4.1_1.zip";
const JAVA_HASH: &str = "00c847d804f4a78e9f04f2683faf14fed898535b177b7fc704486cb0284e9283";
const JAVA_SIZE: u64 = 141_167_264;
const JAVA_PREFIX: &str = "jdk-25.0.4.1+1";

#[derive(Default)]
struct Options {
    game: Option<PathBuf>,
    minecraft: Option<PathBuf>,
    server: Option<PathBuf>,
    java: Option<PathBuf>,
    socket: Option<OsString>,
    world: Option<OsString>,
}

#[derive(Deserialize)]
struct Pins {
    version: String,
    manifest_sha1: String,
    #[serde(default)]
    accepted_manifest_sha1: Vec<String>,
    client_sha1: String,
    server_sha1: String,
    java_major: u32,
}

#[derive(Deserialize, Serialize, PartialEq)]
struct Receipt {
    key: String,
    files: BTreeMap<String, String>,
}

struct Minecraft {
    manifest: PathBuf,
    client: PathBuf,
    libraries: PathBuf,
    assets: PathBuf,
}

fn main() -> ExitCode {
    match run() {
        Ok(code) => ExitCode::from(u8::try_from(code).unwrap_or(1)),
        Err(error) => {
            eprintln!("Minecraft Rust Edition: {error:#}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<i32> {
    let Some(options) = options()? else {
        return Ok(0);
    };
    let exe = env::current_exe()?;
    let native = exe.parent().context("No installation directory")?;
    let pack = native.parent().context("No pack directory")?;
    let game = std::path::absolute(options.game.unwrap_or_else(|| pack.join("game")))?;
    let cache = env::var_os("LANMINECRAFT_NATIVE_CACHE")
        .map(PathBuf::from)
        .unwrap_or_else(|| pack.join("cache"));
    let cache = std::path::absolute(cache)?;
    create_unlinked_dir(&cache)?;
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(child(&cache, "session.lock")?)?;
    FileExt::try_lock_exclusive(&lock)
        .context("The same installation is already preparing or running")?;
    fs::create_dir_all(&game)?;
    let resources = prepare(
        native,
        &cache,
        options.minecraft.map(std::path::absolute).transpose()?,
        options.server.map(std::path::absolute).transpose()?,
        options.java.map(std::path::absolute).transpose()?,
    )?;
    let presentation: Value =
        serde_json::from_reader(File::open(child(native, "presentation.json")?)?)?;
    let title = presentation["name"]
        .as_str()
        .context("Missing pack display name")?;
    ensure!(
        !title.trim().is_empty()
            && title.chars().count() <= 128
            && !title.chars().any(char::is_control),
        "Invalid pack display name"
    );
    let mut client = tool(&native.join("horizon-client.exe"), native);
    client.env("LANMINECRAFT_BUILD_NAME", title);
    if let Some(config) = presentation.get("instanceConfig") {
        let files = config
            .as_object()
            .context("Invalid pack configuration paths")?;
        for (key, value) in files {
            ensure!(
                key.ends_with("_CONFIG")
                    && key
                        .bytes()
                        .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_'),
                "Invalid pack configuration key"
            );
            let path = Path::new(
                value
                    .as_str()
                    .context("Missing relative pack configuration path")?,
            );
            client.env(key, child_path(&game, path)?);
        }
    }
    client
        .args(["--resources"])
        .arg(&resources)
        .arg("--game-dir")
        .arg(&game)
        .arg("--home")
        .arg(&game);
    let profile = child(&cache, "process-home")?;
    let temp = child(&cache, "process-temp")?;
    remove_tree(&temp)?;
    configure_profile(&mut client, &profile, &temp)?;
    client.env("HORIZON_HOME", &game);
    if let Some(socket) = options.socket {
        client.arg("--control-socket").arg(socket);
    }
    if let Some(world) = options.world {
        client.arg("--quickPlaySingleplayer").arg(world);
    }
    let status = client
        .status()
        .context("Could not start the Horizon game client");
    remove_tree(&temp)?;
    let status = status?;
    Ok(status.code().unwrap_or(1))
}

fn options() -> Result<Option<Options>> {
    let mut result = Options::default();
    let mut args = env::args_os().skip(1);
    while let Some(arg) = args.next() {
        let name = arg.to_str().context("Invalid option")?;
        if name == "--help" {
            println!(
                "launch-rust-edition [--game-dir DIR] [--minecraft-dir DIR] [--server-jar PATH] [--java-home DIR]"
            );
            return Ok(None);
        }
        let value = args
            .next()
            .with_context(|| format!("Missing value for {name}"))?;
        match name {
            "--game-dir" => result.game = Some(PathBuf::from(value)),
            "--minecraft-dir" => result.minecraft = Some(PathBuf::from(value)),
            "--server-jar" => result.server = Some(PathBuf::from(value)),
            "--java-home" => result.java = Some(PathBuf::from(value)),
            "--control-socket" => result.socket = Some(value),
            "--quickPlaySingleplayer" => result.world = Some(value),
            _ => bail!("Unknown option {name}"),
        }
    }
    Ok(Some(result))
}

fn tool(exe: &Path, cwd: &Path) -> Command {
    let mut command = Command::new(exe);
    command.current_dir(cwd);
    for (key, _) in env::vars_os() {
        if key
            .to_string_lossy()
            .to_ascii_uppercase()
            .starts_with("HORIZON_")
        {
            command.env_remove(key);
        }
    }
    command
}

fn configure_profile(command: &mut Command, home: &Path, temp: &Path) -> Result<()> {
    let roaming = child(home, "AppData/Roaming")?;
    let local = child(home, "AppData/Local")?;
    let config = child(home, "Config")?;
    create_unlinked_dir(&roaming)?;
    create_unlinked_dir(&local)?;
    create_unlinked_dir(&config)?;
    create_unlinked_dir(temp)?;
    command
        .env("TEMP", temp)
        .env("TMP", temp)
        .env("TMPDIR", temp)
        .env("HOME", home)
        .env("USERPROFILE", home)
        .env("APPDATA", &roaming)
        .env("LOCALAPPDATA", &local)
        .env("XDG_CACHE_HOME", &local)
        .env("XDG_CONFIG_HOME", config)
        .env("XDG_DATA_HOME", roaming);
    Ok(())
}
fn prepare(
    native: &Path,
    cache: &Path,
    minecraft: Option<PathBuf>,
    server: Option<PathBuf>,
    java: Option<PathBuf>,
) -> Result<PathBuf> {
    let preparer = child(native, "horizon-prepare.exe")?;
    let research = child(native, "research-lock.json")?;
    let lock: Value = serde_json::from_reader(File::open(&research)?)?;
    let pins: Pins = serde_json::from_value(lock["minecraft"].clone())?;
    let key = format!(
        "{}:{}",
        hash::<Sha256>(&preparer)?,
        hash::<Sha256>(&research)?
    );
    let resources = child(cache, "resources")?;
    let receipt = child(cache, "resources.json")?;
    let verify_started = Instant::now();
    if resources.is_dir()
        && let Ok(file) = File::open(&receipt)
        && let Ok(old) = serde_json::from_reader::<_, Receipt>(file)
        && old.key == key
        && !old.files.is_empty()
        && verify_inventory(&resources, &old.files)?
    {
        if server.is_some() {
            let input = find_minecraft(&pins, minecraft)?;
            server_jar(cache, &input.manifest, &pins, server)?;
        }
        prepared_data::reconcile(&resources, &old.files)?;
        eprintln!(
            "Verified {} cached Horizon resource files in {:.2}s",
            old.files.len(),
            verify_started.elapsed().as_secs_f64()
        );
        return Ok(resources);
    }

    let input = find_minecraft(&pins, minecraft)?;
    let server = server_jar(cache, &input.manifest, &pins, server)?;
    let jdk = java_home(cache, java, pins.java_major)?;
    let scratch = child(cache, "preparation")?;
    remove_tree(&scratch)?;
    remove_tree(&resources)?;
    if receipt.exists() {
        fs::remove_file(&receipt)?;
    }
    let result = (|| {
        for relative in [
            "exporters",
            "crates/horizon-prepare",
            "mods/horizon/src/test/java",
        ] {
            copy_tree(&child(native, relative)?, &child(&scratch, relative)?)?;
        }
        fs::copy(&research, scratch.join("research-lock.json"))?;
        let temp = scratch.join("tmp");
        let home = scratch.join("home");

        let mut command = tool(&preparer, &scratch);
        command
            .arg("--prepare")
            .arg("--root")
            .arg(&scratch)
            .arg("--output")
            .arg(&resources)
            .arg("--version-json")
            .arg(&input.manifest)
            .arg("--client-jar")
            .arg(&input.client)
            .arg("--libraries-dir")
            .arg(&input.libraries)
            .arg("--assets-dir")
            .arg(&input.assets)
            .arg("--server-jar")
            .arg(server)
            .arg("--java-home")
            .arg(&jdk)
            .args(["--complete", "--progress", "json"]);
        for key in [
            "JAVA_TOOL_OPTIONS",
            "JDK_JAVA_OPTIONS",
            "_JAVA_OPTIONS",
            "CLASSPATH",
        ] {
            command.env_remove(key);
        }
        configure_profile(&mut command, &home, &temp)?;
        command.env("JAVA_HOME", &jdk).env(
            "JAVA_TOOL_OPTIONS",
            format!(
                "-Duser.home=\"{}\" -Djava.io.tmpdir=\"{}\"",
                home.display(),
                temp.display()
            ),
        );
        ensure!(
            command.status()?.success(),
            "Horizon resource preparation failed"
        );
        let files = inventory(&resources)?;
        ensure!(!files.is_empty(), "Horizon did not create any resources");
        prepared_data::reconcile(&resources, &files)?;
        let pending = child(cache, "resources.json.part")?;
        serde_json::to_writer(File::create(&pending)?, &Receipt { key, files })?;
        fs::rename(pending, &receipt)?;
        Ok(())
    })();
    let cleaned = remove_tree(&scratch);
    if result.is_err() {
        remove_tree(&resources)?;
    }
    result.and(cleaned)?;
    Ok(resources)
}

fn find_minecraft(pins: &Pins, explicit: Option<PathBuf>) -> Result<Minecraft> {
    let candidates = if let Some(explicit) = explicit {
        vec![explicit]
    } else {
        let mut roots = Vec::new();
        roots.extend(env::var_os("LANMINECRAFT_SHARED_MINECRAFT").map(PathBuf::from));
        roots.extend(env::var_os("APPDATA").map(|path| PathBuf::from(path).join(".minecraft")));
        roots
    };
    for root in candidates {
        let dir = root.join("versions").join(&pins.version);
        let input = Minecraft {
            manifest: dir.join(format!("{}.json", pins.version)),
            client: dir.join(format!("{}.jar", pins.version)),
            libraries: root.join("libraries"),
            assets: root.join("assets"),
        };
        if !input.manifest.is_file()
            || !input.client.is_file()
            || !input.libraries.is_dir()
            || !input.assets.is_dir()
        {
            continue;
        }
        let manifest_hash = hash::<Sha1>(&input.manifest)?;
        if (pins.manifest_sha1 == manifest_hash
            || pins.accepted_manifest_sha1.contains(&manifest_hash))
            && hash::<Sha1>(&input.client)? == pins.client_sha1
        {
            return Ok(input);
        }
    }
    bail!(
        "Local Minecraft Java Edition {} files are required. Point --minecraft-dir at a complete installation.",
        pins.version
    )
}

fn server_jar(
    cache: &Path,
    manifest: &Path,
    pins: &Pins,
    explicit: Option<PathBuf>,
) -> Result<PathBuf> {
    let expected = pins.server_sha1.as_str();
    let version: Value = serde_json::from_reader(File::open(manifest)?)?;
    let entry = &version["downloads"]["server"];
    let size = entry["size"]
        .as_u64()
        .context("Missing Mojang server size")?;
    let url = format!("https://piston-data.mojang.com/v1/objects/{expected}/server.jar");
    ensure!(
        size > 0
            && size <= 256 * 1024 * 1024
            && entry["url"].as_str() == Some(&url)
            && entry["sha1"].as_str() == Some(expected),
        "Mojang server does not match Horizon's pin"
    );
    if let Some(path) = explicit {
        ensure!(
            verified_file::<Sha1>(&path, size, expected)?,
            "--server-jar does not match Minecraft {}: {}",
            pins.version,
            path.display()
        );
        return Ok(path);
    }

    let cached = child(cache, "server.jar")?;
    for candidate in [
        cached.clone(),
        manifest.with_file_name("server.jar"),
        manifest.with_file_name(format!("{}-server.jar", pins.version)),
    ] {
        if verified_file::<Sha1>(&candidate, size, expected)? {
            return Ok(candidate);
        }
    }
    download::<Sha1>(&url, size, expected, &cached)
}

fn java_home(cache: &Path, explicit: Option<PathBuf>, major: u32) -> Result<PathBuf> {
    ensure!(major == 25, "Unsupported Horizon preparation Java {major}");
    let profile = child(cache, "java-tool-home")?;
    let temp = child(cache, "java-tool-temp")?;
    if let Some(home) = explicit {
        ensure!(
            is_java(&home, major, &profile, &temp),
            "--java-home does not point to Java {major}"
        );
        return Ok(home);
    }
    let shared = env::var_os("LANMINECRAFT_SHARED_MINECRAFT").and_then(|path| {
        Some(
            PathBuf::from(path)
                .parent()?
                .join("JavaRuntimes/runtime/windows-x64/java-25"),
        )
    });
    let cached = child(cache, "java-25")?;
    for candidate in [
        shared,
        env::var_os("JAVA_HOME").map(PathBuf::from),
        Some(cached.clone()),
    ]
    .into_iter()
    .flatten()
    {
        if is_java(&candidate, major, &profile, &temp) {
            return Ok(candidate);
        }
    }
    let archive = child(cache, "java-25.zip")?;
    if !verified_file::<Sha256>(&archive, JAVA_SIZE, JAVA_HASH)? {
        download::<Sha256>(JAVA_URL, JAVA_SIZE, JAVA_HASH, &archive)?;
    }
    let staging = child(cache, "java-25-stage")?;
    remove_tree(&staging)?;
    create_unlinked_dir(&staging)?;
    let result = (|| {
        let mut zip = zip::ZipArchive::new(File::open(&archive)?)?;
        for index in 0..zip.len() {
            let mut file = zip.by_index(index)?;
            let path = file.enclosed_name().context("Unsafe Java archive path")?;
            let path = path
                .strip_prefix(JAVA_PREFIX)
                .context("Unexpected Java archive root")?;
            if path.as_os_str().is_empty() {
                continue;
            }
            let target = child_path(&staging, path)?;
            if file.is_dir() {
                fs::create_dir_all(target)?;
            } else {
                fs::create_dir_all(target.parent().context("Missing Java archive parent")?)?;
                io::copy(&mut file, &mut File::create(target)?)?;
            }
        }
        ensure!(
            is_java(&staging, major, &profile, &temp),
            "Java archive does not run as Java {major}"
        );
        remove_tree(&cached)?;
        fs::rename(&staging, &cached)?;
        fs::remove_file(&archive)?;
        Ok(cached)
    })();
    remove_tree(&staging)?;
    result
}

fn is_java(home: &Path, major: u32, profile: &Path, temp: &Path) -> bool {
    let java = home.join("bin/java.exe");
    if !java.is_file() {
        return false;
    }
    let mut command = Command::new(java);
    for key in [
        "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "_JAVA_OPTIONS",
        "CLASSPATH",
    ] {
        command.env_remove(key);
    }
    if configure_profile(&mut command, profile, temp).is_err() {
        return false;
    }
    command
        .arg(format!("-Duser.home={}", profile.display()))
        .arg(format!("-Djava.io.tmpdir={}", temp.display()))
        .arg("-version")
        .output()
        .is_ok_and(|result| {
            result.status.success()
                && String::from_utf8_lossy(&result.stderr).contains(&format!("version \"{major}."))
        })
}

fn verified_file<D: Digest>(path: &Path, size: u64, expected: &str) -> Result<bool> {
    Ok(path.is_file() && path.metadata()?.len() == size && hash::<D>(path)? == expected)
}

fn download<D: Digest>(url: &str, size: u64, expected: &str, target: &Path) -> Result<PathBuf> {
    let partial = target.with_extension("part");
    ensure_unlinked(&partial)?;
    let result = (|| {
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(30))
            .timeout_read(Duration::from_secs(60))
            .build();
        let response = agent
            .get(url)
            .set("User-Agent", "LANMinecraft-Rust-Edition")
            .call()?;
        let mut reader = response.into_reader().take(size + 1);
        let written = io::copy(&mut reader, &mut File::create(&partial)?)?;
        ensure!(
            written == size && hash::<D>(&partial)? == expected,
            "Download failed verification: {url}"
        );
        if target.exists() {
            fs::remove_file(target)?;
        }
        fs::rename(&partial, target)?;
        Ok(target.to_owned())
    })();
    if partial.exists() {
        fs::remove_file(&partial)?;
    }
    result
}

fn hash<D: Digest>(path: &Path) -> Result<String> {
    hash_reader::<D>(File::open(path)?, &mut [0; 65536])
}

fn hash_reader<D: Digest>(mut reader: impl Read, buffer: &mut [u8]) -> Result<String> {
    let mut digest = D::new();
    loop {
        let count = reader.read(buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let bytes = digest.finalize();
    let mut text = String::with_capacity(bytes.len() * 2);
    for &byte in &bytes {
        text.push(char::from(HEX[(byte >> 4) as usize]));
        text.push(char::from(HEX[(byte & 15) as usize]));
    }
    Ok(text)
}

fn resource_files(root: &Path) -> Result<Vec<PathBuf>> {
    let mut files = tree_files(root)?;
    // Horizon generates this decoded-sprite cache after preparation and
    // independently checks its format and texture fingerprint on each read.
    files.retain(|file| {
        file.parent() != Some(root)
            || !file
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| {
                    name == "horizon-sprites.cache" || prepared_data::generated(name)
                })
    });
    Ok(files)
}

fn resource_name(root: &Path, file: &Path) -> Result<String> {
    Ok(file
        .strip_prefix(root)?
        .to_str()
        .context("Invalid resource path")?
        .replace('\\', "/"))
}

fn inventory(root: &Path) -> Result<BTreeMap<String, String>> {
    resource_files(root)?
        .into_iter()
        .map(|file| Ok((resource_name(root, &file)?, hash::<Sha256>(&file)?)))
        .collect()
}

fn verify_inventory(root: &Path, expected: &BTreeMap<String, String>) -> Result<bool> {
    let files = resource_files(root)?;
    if files.len() != expected.len() {
        return Ok(false);
    }
    let workers = std::thread::available_parallelism().map_or(1, |count| count.get().min(8));
    let batch_size = files.len().div_ceil(workers).max(1);
    std::thread::scope(|scope| {
        let tasks: Vec<_> = files
            .chunks(batch_size)
            .map(|batch| {
                scope.spawn(move || -> Result<bool> {
                    let mut buffer = [0; 65536];
                    for file in batch {
                        let name = resource_name(root, file)?;
                        let Some(digest) = expected.get(&name) else {
                            return Ok(false);
                        };
                        if hash_reader::<Sha256>(File::open(file)?, &mut buffer)? != *digest {
                            return Ok(false);
                        }
                    }
                    Ok(true)
                })
            })
            .collect();
        let mut verified = true;
        for task in tasks {
            verified &= task
                .join()
                .map_err(|_| anyhow::anyhow!("Resource verifier stopped"))??;
        }
        Ok(verified)
    })
}

fn tree_files(root: &Path) -> Result<Vec<PathBuf>> {
    ensure_unlinked(root)?;
    let mut files = Vec::new();
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        let path = entry.path();
        // Windows already provides size and attributes while enumerating a
        // directory. This metadata does not follow a reparse point.
        let info = entry.metadata()?;
        ensure!(
            info.file_attributes() & REPARSE_POINT == 0,
            "Linked path refused: {}",
            path.display()
        );
        if info.is_dir() {
            files.extend(tree_files(&path)?);
        } else {
            files.push(path);
        }
    }
    Ok(files)
}

fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    for file in tree_files(source)? {
        let target = child_path(destination, file.strip_prefix(source)?)?;
        fs::create_dir_all(target.parent().context("Missing preparation folder")?)?;
        fs::copy(&file, target)?;
    }
    Ok(())
}

fn child(root: &Path, relative: &str) -> Result<PathBuf> {
    child_path(root, Path::new(relative))
}

fn child_path(root: &Path, relative: &Path) -> Result<PathBuf> {
    use std::path::Component;
    ensure!(
        !relative.as_os_str().is_empty()
            && relative
                .components()
                .all(|part| matches!(part, Component::Normal(_))),
        "Unsafe preparation path {}",
        relative.display()
    );
    let path = root.join(relative);
    for parent in path.ancestors() {
        ensure_unlinked(parent)?;
    }
    Ok(path)
}

fn create_unlinked_dir(path: &Path) -> Result<()> {
    for parent in path.ancestors() {
        ensure_unlinked(parent)?;
    }
    fs::create_dir_all(path)?;
    Ok(())
}

fn ensure_unlinked(path: &Path) -> Result<Option<fs::Metadata>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            ensure!(
                metadata.file_attributes() & REPARSE_POINT == 0,
                "Linked path refused: {}",
                path.display()
            );
            Ok(Some(metadata))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

fn remove_tree(path: &Path) -> Result<()> {
    if ensure_unlinked(path)?.is_some() {
        tree_files(path)?;
        fs::remove_dir_all(path)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const SERVER_BYTES: &[u8] = b"verified server";

    fn with_server_fixture(
        name: &str,
        test: impl FnOnce(&Path, &Path, &Pins) -> Result<()>,
    ) -> Result<()> {
        let root = env::temp_dir().join(format!("lanmc-{name}-{}", std::process::id()));
        fs::create_dir(&root)?;
        let result = (|| {
            let cache = root.join("cache");
            fs::create_dir(&cache)?;
            let server_sha1 = format!("{:x}", Sha1::digest(SERVER_BYTES));
            let manifest = root.join("26.3.json");
            let version = serde_json::json!({
                "downloads": { "server": {
                    "sha1": server_sha1,
                    "size": SERVER_BYTES.len(),
                    "url": format!("https://piston-data.mojang.com/v1/objects/{server_sha1}/server.jar")
                } }
            });
            serde_json::to_writer(File::create(&manifest)?, &version)?;
            let pins = Pins {
                version: "26.3".into(),
                manifest_sha1: hash::<Sha1>(&manifest)?,
                accepted_manifest_sha1: Vec::new(),
                client_sha1: String::new(),
                server_sha1,
                java_major: 25,
            };
            test(&cache, &manifest, &pins)
        })();
        fs::remove_dir_all(root)?;
        result
    }

    #[test]
    fn server_selection_reuses_verified_cache_and_local_siblings() -> Result<()> {
        with_server_fixture("server-reuse", |cache, manifest, pins| {
            let cached = cache.join("server.jar");
            let local = manifest.with_file_name("server.jar");
            let versioned = manifest.with_file_name("26.3-server.jar");
            fs::write(&cached, SERVER_BYTES)?;
            fs::write(&local, SERVER_BYTES)?;
            assert_eq!(server_jar(cache, manifest, pins, None)?, cached);

            fs::write(&cached, b"corrupt! server")?;
            assert_eq!(server_jar(cache, manifest, pins, None)?, local);

            fs::write(&local, b"corrupt! server")?;
            fs::write(&versioned, SERVER_BYTES)?;
            assert_eq!(server_jar(cache, manifest, pins, None)?, versioned);
            assert_eq!(fs::read(&cached)?, b"corrupt! server");
            assert_eq!(fs::read(&local)?, b"corrupt! server");
            Ok(())
        })
    }

    #[test]
    fn explicit_server_selection_never_falls_back() -> Result<()> {
        with_server_fixture("server-explicit", |cache, manifest, pins| {
            fs::write(cache.join("server.jar"), SERVER_BYTES)?;
            let explicit = manifest.with_file_name("my server.jar");
            for contents in [None, Some(b"short".as_slice()), Some(b"corrupt! server")] {
                if let Some(contents) = contents {
                    fs::write(&explicit, contents)?;
                }
                let error = server_jar(cache, manifest, pins, Some(explicit.clone()))
                    .expect_err("An invalid explicit server must never fall back");
                assert!(error.to_string().contains("--server-jar"));
            }

            fs::write(&explicit, SERVER_BYTES)?;
            assert_eq!(
                server_jar(cache, manifest, pins, Some(explicit.clone()))?,
                explicit
            );
            Ok(())
        })
    }

    #[test]
    fn warm_resources_reject_changed_bytes_and_paths() -> Result<()> {
        let root = env::temp_dir().join(format!("lanmc-receipt-test-{}", std::process::id()));
        fs::create_dir(&root)?;
        let result = (|| {
            let data = root.join("data.json");
            fs::write(&data, b"original")?;
            let expected = inventory(&root)?;
            let modified = data.metadata()?.modified()?;
            fs::write(root.join("horizon-sprites.cache"), b"mutable")?;
            ensure!(verify_inventory(&root, &expected)?);

            fs::write(&data, b"modified")?;
            File::options()
                .write(true)
                .open(&data)?
                .set_modified(modified)?;
            ensure!(!verify_inventory(&root, &expected)?);

            fs::write(&data, b"original")?;
            fs::rename(&data, root.join("other.json"))?;
            ensure!(!verify_inventory(&root, &expected)?);
            Ok(())
        })();
        fs::remove_dir_all(&root)?;
        result
    }
}
