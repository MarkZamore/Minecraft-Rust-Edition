//! Host and admit a Horizon world through the launcher-owned Steam identity.
use lanmc_steam_native::SteamBridge;
use sha1::{Digest, Sha1};
use std::{
    collections::BTreeMap,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::{Mutex, OnceLock},
};

const UPSTREAM_PIN: &str = "66f3981f522b24c250b21523a70080965b940424";
const UUID_NAMESPACE: u128 = 0x3b26fe30_fc0f_44d9_8a9e_08c3bde54df1;
const ADDRESS_PREFIX: &str = "lanmc-steam:";

struct Identity {
    uuid: u128,
    name: String,
    known: BTreeMap<u64, u128>,
    bridge: SteamBridge,
}

static STATE: OnceLock<Identity> = OnceLock::new();
static NOTICE: Mutex<(bool, Option<String>)> = Mutex::new((false, None));

pub fn init() -> Result<(), String> {
    let Some(id) = std::env::var_os("LANMINECRAFT_STEAM_ID") else {
        return Ok(());
    };
    let id = id
        .to_str()
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|id| *id > 0)
        .ok_or("Invalid launcher Steam identity")?;
    let uuid = std::env::var("LANMINECRAFT_PLAYER_UUID")
        .ok()
        .as_deref()
        .and_then(parse_uuid)
        .ok_or("Invalid launcher player UUID")?;
    let name = std::env::var("LANMINECRAFT_PLAYER_NAME")
        .ok()
        .ok_or("Invalid launcher player name")?;
    let known = known_identities(
        &std::env::var("LANMINECRAFT_KNOWN_STEAM_IDENTITIES").unwrap_or_default(),
    )?;
    if known.get(&id).copied().unwrap_or_else(|| portable_uuid(id)) != uuid {
        return Err("Launcher Steam identity does not match the player UUID".into());
    }
    let version = std::env::var("LANMINECRAFT_NATIVE_VARIANT")
        .ok()
        .filter(|version| {
            !version.is_empty()
                && version.len() <= 64
                && version
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b".-_".contains(&b))
        })
        .ok_or("Invalid launcher native runtime version")?;
    let pin = format!("{UPSTREAM_PIN}:{version}");
    let variant = format!("horizon-{:x}", sha2::Sha256::digest(pin.as_bytes()));
    let bridge = SteamBridge::start(id, &variant, &name)?;
    STATE
        .set(Identity {
            uuid,
            name,
            known,
            bridge,
        })
        .map_err(|_| "Steam identity already initialized".into())
}

pub fn active() -> bool {
    STATE.get().is_some()
}

pub fn owner_name() -> Option<&'static str> {
    STATE.get().map(|identity| identity.name.as_str())
}

pub fn owner_uuid(name: &str) -> Option<u128> {
    STATE
        .get()
        .filter(|state| state.name == name)
        .map(|state| state.uuid)
}

pub fn bind_address() -> Option<IpAddr> {
    active().then_some(Ipv4Addr::LOCALHOST.into())
}

pub fn publish(port: u16, world: &str) -> Result<(), String> {
    if let Some(state) = STATE.get() {
        state.bridge.publish(port, world, &state.name)?;
        *NOTICE.lock().unwrap() = (false, None);
    }
    Ok(())
}

pub fn is_published() -> bool {
    STATE.get().is_some_and(|state| state.bridge.is_published())
}

pub fn publication_failed() -> bool {
    STATE.get().is_some_and(|state| {
        !state.bridge.is_published() && state.bridge.last_error().is_some()
    })
}

pub fn unpublish() {
    if let Some(state) = STATE.get() {
        state.bridge.unpublish();
    }
}

pub fn address(lobby_id: u64) -> String {
    format!("{ADDRESS_PREFIX}{lobby_id}")
}

pub fn worlds() -> Vec<lanmc_steam_native::WorldOffer> {
    STATE
        .get()
        .map_or_else(Vec::new, |state| state.bridge.worlds())
}

pub fn join(address: &str) -> Result<Option<SocketAddr>, String> {
    let Some(lobby_id) = address.strip_prefix(ADDRESS_PREFIX) else {
        return Ok(None);
    };
    let lobby_id = lobby_id.parse().map_err(|_| "Invalid Steam world")?;
    let state = STATE.get().ok_or("Steam is unavailable")?;
    state.bridge.join(lobby_id).map(Some)
}

pub fn claim_guest(address: SocketAddr) -> Option<(u128, String)> {
    let state = STATE.get()?;
    let peer = state.bridge.claim_guest(address)?;
    let uuid = state
        .known
        .get(&peer.steam_id)
        .copied()
        .unwrap_or_else(|| portable_uuid(peer.steam_id));
    Some((uuid, peer.nickname))
}

pub fn sharing_notice() -> Option<String> {
    let state = STATE.get()?;
    let mut seen = NOTICE.lock().ok()?;
    let published = state.bridge.is_published();
    let error = state.bridge.last_error();
    let message = if error.is_some() && error != seen.1 {
        error.clone()
    } else if published && !seen.0 {
        Some(opened_label().into())
    } else {
        None
    };
    *seen = (published, error);
    message
}

fn label(en: &'static str, ru: &'static str) -> &'static str {
    if crate::lang::code() == "ru_ru" {
        ru
    } else {
        en
    }
}

fn opened_label() -> &'static str {
    label(
        "World open to Steam friends",
        "Мир открыт для друзей в Steam",
    )
}

pub fn steam_only_label() -> &'static str {
    label(
        "Use Open to LAN in the pause menu to share this world on Steam",
        "Чтобы открыть мир в Steam, нажмите «Открыть для сети» в меню паузы",
    )
}

pub fn scan_label() -> &'static str {
    label("Looking for Steam worlds", "Поиск миров Steam")
}

fn known_identities(text: &str) -> Result<BTreeMap<u64, u128>, &'static str> {
    let mut known = BTreeMap::new();
    for item in text.split(',').filter(|item| !item.is_empty()) {
        let (id, uuid) = item.split_once('=').ok_or("Invalid known Steam identity")?;
        let id = id
            .parse::<u64>()
            .ok()
            .filter(|id| *id > 0)
            .ok_or("Invalid known Steam ID")?;
        let uuid = parse_uuid(uuid).ok_or("Invalid known Steam UUID")?;
        if known.insert(id, uuid).is_some() {
            return Err("Duplicate known Steam ID");
        }
    }
    Ok(known)
}

pub fn parse_uuid(text: &str) -> Option<u128> {
    let bytes = text.as_bytes();
    if bytes.len() != 36 || [8, 13, 18, 23].iter().any(|&i| bytes[i] != b'-') {
        return None;
    }
    let hex: String = text.chars().filter(|c| *c != '-').collect();
    if hex.len() != 32 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return None;
    }
    u128::from_str_radix(&hex, 16)
        .ok()
        .filter(|uuid| *uuid != 0)
}

fn portable_uuid(id: u64) -> u128 {
    let mut sha = Sha1::new();
    sha.update(UUID_NAMESPACE.to_be_bytes());
    sha.update(format!("steam:{id}").as_bytes());
    let digest = sha.finalize();
    let mut uuid: [u8; 16] = digest[..16].try_into().expect("SHA-1 has 20 bytes");
    uuid[6] = (uuid[6] & 0x0f) | 0x50;
    uuid[8] = (uuid[8] & 0x3f) | 0x80;
    u128::from_be_bytes(uuid)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn steam_uuid_matches_launcher_derivation() {
        assert_eq!(
            portable_uuid(76561197960265729),
            0x535f8bea_8e65_5367_a3d5_da0dd6c5c31d
        );
    }

    #[test]
    fn known_overrides_accept_canonical_nonzero_uuids_only() {
        assert!(known_identities("1=00000000-0000-0000-0000-000000000000").is_err());
        assert!(known_identities("1=incorrect").is_err());
        assert_eq!(
            known_identities("1=535f8bea-8e65-5367-a3d5-da0dd6c5c31d")
                .unwrap()
                .get(&1),
            Some(&0x535f8bea_8e65_5367_a3d5_da0dd6c5c31d),
        );
    }
}
