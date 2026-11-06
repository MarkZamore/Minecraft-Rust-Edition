//! Reads the launcher's SHA256-pinned local skin registry for portable profiles.
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    io::Read,
    sync::{Arc, Mutex, OnceLock, mpsc},
    time::{Duration, Instant},
};

const MAX_REGISTRY_BYTES: u64 = 256 * 1024;
const MAX_SKIN_BYTES: u64 = 8 * 1024 * 1024;
const MAX_SKINS: usize = 64;
const MAX_DOWNLOADS: usize = 4;

#[derive(Clone)]
struct Profile {
    hash: String,
    url: String,
    slim: bool,
}

pub(crate) struct SelectedSkin {
    pub location: String,
    pub rgba: Arc<Vec<u8>>,
    pub slim: bool,
}

enum Download {
    Pending(mpsc::Receiver<Option<Vec<u8>>>),
    Ready(Arc<Vec<u8>>),
    RetryAt(Instant),
}

#[derive(Default)]
struct Cache {
    refresh: Option<Instant>,
    profiles: HashMap<u128, Profile>,
    downloads: HashMap<String, Download>,
}

static CACHE: OnceLock<Mutex<Cache>> = OnceLock::new();

pub(crate) fn selected(uuid: u128) -> Option<SelectedSkin> {
    let registry = std::env::var_os("LANMINECRAFT_SKIN_REGISTRY")?;
    let mut cache = CACHE.get_or_init(Mutex::default).lock().ok()?;
    let now = Instant::now();
    if cache
        .refresh
        .is_none_or(|last| now.duration_since(last) >= Duration::from_secs(1))
    {
        let text = std::fs::File::open(registry).ok().and_then(|file| {
            let mut text = String::new();
            file.take(MAX_REGISTRY_BYTES + 1)
                .read_to_string(&mut text)
                .ok()?;
            (text.len() as u64 <= MAX_REGISTRY_BYTES).then_some(text)
        });
        cache.profiles = text
            .as_deref()
            .unwrap_or_default()
            .lines()
            .filter_map(parse_profile)
            .take(MAX_SKINS)
            .collect();
        let Cache {
            profiles,
            downloads,
            ..
        } = &mut *cache;
        downloads.retain(|hash, value| {
            matches!(value, Download::Pending(_))
                || profiles.values().any(|profile| profile.hash == *hash)
        });
        cache.refresh = Some(now);
    }
    for value in cache.downloads.values_mut() {
        if let Download::Pending(result) = value {
            match result.try_recv() {
                Ok(Some(rgba)) => *value = Download::Ready(Arc::new(rgba)),
                Ok(None) | Err(mpsc::TryRecvError::Disconnected) => {
                    *value = Download::RetryAt(now + Duration::from_secs(5));
                }
                Err(mpsc::TryRecvError::Empty) => {}
            }
        }
    }
    let profile = cache.profiles.get(&uuid)?.clone();
    match cache.downloads.get(&profile.hash) {
        Some(Download::Ready(rgba)) => Some(SelectedSkin {
            location: format!("lanmc/skin/{}", profile.hash),
            rgba: rgba.clone(),
            slim: profile.slim,
        }),
        Some(Download::Pending(_)) => None,
        Some(Download::RetryAt(time)) if *time > now => None,
        _ if cache
            .downloads
            .values()
            .filter(|value| matches!(value, Download::Pending(_)))
            .count()
            >= MAX_DOWNLOADS =>
        {
            None
        }
        _ => {
            let (send, receive) = mpsc::channel();
            let hash = profile.hash.clone();
            if std::thread::Builder::new()
                .name("launcher-skin".into())
                .spawn(move || {
                    let _ = send.send(download(&profile));
                })
                .is_ok()
            {
                cache.downloads.insert(hash, Download::Pending(receive));
            }
            None
        }
    }
}

fn parse_profile(line: &str) -> Option<(u128, Profile)> {
    let mut fields = line.split('|');
    let id = fields.next()?;
    let hash = fields.next()?;
    let model = fields.next()?;
    let url = fields.next()?;
    if fields.next().is_some() || hash.len() != 64 || !hash.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let uuid = crate::lanmc_steam::parse_uuid(id)?;
    let slim = match model {
        "slim" => true,
        "classic" => false,
        _ => return None,
    };
    let (port, path) = url.strip_prefix("http://127.0.0.1:")?.split_once('/')?;
    if port.parse::<u16>().ok()? == 0 || path != format!("skin/{id}/{hash}") {
        return None;
    }
    Some((
        uuid,
        Profile {
            hash: hash.to_ascii_lowercase(),
            url: url.to_owned(),
            slim,
        },
    ))
}

fn download(profile: &Profile) -> Option<Vec<u8>> {
    let response = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(2))
        .redirects(0)
        .try_proxy_from_env(false)
        .build()
        .get(&profile.url)
        .call()
        .ok()?;
    if response.status() != 200 {
        return None;
    }
    let mut png = Vec::new();
    response
        .into_reader()
        .take(MAX_SKIN_BYTES + 1)
        .read_to_end(&mut png)
        .ok()?;
    if png.len() as u64 > MAX_SKIN_BYTES
        || png.len() < 24
        || !png.starts_with(b"\x89PNG\r\n\x1a\n")
        || png[12..16] != *b"IHDR"
        || png[16..20] != 64_u32.to_be_bytes()
        || ![32_u32.to_be_bytes(), 64_u32.to_be_bytes()]
            .iter()
            .any(|height| png[20..24] == *height)
        || format!("{:x}", Sha256::digest(&png)) != profile.hash
    {
        return None;
    }
    let (width, height, rgba) = crate::hud::decode_bytes(&png).ok()?;
    crate::skins::process_legacy_skin(width, height, &rgba)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_matching_local_registry_paths() {
        let id = "535f8bea-8e65-5367-a3d5-da0dd6c5c31d";
        let hash = "a".repeat(64);
        let line = |host: &str, model: &str| {
            format!("{id}|{hash}|{model}|http://{host}:1234/skin/{id}/{hash}")
        };
        assert!(parse_profile(&line("127.0.0.1", "slim")).unwrap().1.slim);
        assert!(!parse_profile(&line("127.0.0.1", "classic")).unwrap().1.slim);
        assert!(parse_profile(&line("example.com", "slim")).is_none());
        assert!(parse_profile(&line("127.0.0.1", "undefined")).is_none());
        assert!(parse_profile(&line("127.0.0.1", "slim").replace("/skin/", "/other/")).is_none());
    }
}
