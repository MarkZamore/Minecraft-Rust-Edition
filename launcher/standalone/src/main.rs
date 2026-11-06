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
    time::Duration,
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
                "launch-rust-edition [--game-dir DIR] [--minecraft-dir DIR] [--java-home DIR]"
            );
            return Ok(None);
        }
        let value = args
            .next()
            .with_context(|| format!("Missing value for {name}"))?;
        match name {
            "--game-dir" => result.game = Some(PathBuf::from(value)),
            "--minecraft-dir" => result.minecraft = Some(PathBuf::from(value)),
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
    if resources.is_dir()
        && let Ok(file) = File::open(&receipt)
        && let Ok(old) = serde_json::from_reader::<_, Receipt>(file)
        && old.key == key
        && !old.files.is_empty()
        && old.files == inventory(&resources)?
    {
        return Ok(resources);
    }

    let input = find_minecraft(&pins, minecraft)?;
    let server = server_jar(cache, &input.manifest, &pins.server_sha1)?;
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

fn server_jar(cache: &Path, manifest: &Path, expected: &str) -> Result<PathBuf> {
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
    download::<Sha1>(&url, size, expected, &child(cache, "server.jar")?)
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
    let archive = download::<Sha256>(
        JAVA_URL,
        JAVA_SIZE,
        JAVA_HASH,
        &child(cache, "java-25.zip")?,
    )?;
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

fn download<D: Digest>(url: &str, size: u64, expected: &str, target: &Path) -> Result<PathBuf> {
    if target.is_file() && target.metadata()?.len() == size && hash::<D>(target)? == expected {
        return Ok(target.to_owned());
    }
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
    let mut digest = D::new();
    let mut file = File::open(path)?;
    let mut buffer = [0_u8; 65536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn inventory(root: &Path) -> Result<BTreeMap<String, String>> {
    let mut files = BTreeMap::new();
    for file in tree_files(root)? {
        let name = file
            .strip_prefix(root)?
            .to_str()
            .context("Invalid resource path")?
            .replace('\\', "/");
        // Horizon generates this decoded-sprite cache after preparation and
        // independently checks its format and texture fingerprint on each read.
        if name != "horizon-sprites.cache" {
            files.insert(name, hash::<Sha256>(&file)?);
        }
    }
    Ok(files)
}

fn tree_files(root: &Path) -> Result<Vec<PathBuf>> {
    ensure_unlinked(root)?;
    let mut files = Vec::new();
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        let info = ensure_unlinked(&path)?.context("A preparation file disappeared")?;
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
