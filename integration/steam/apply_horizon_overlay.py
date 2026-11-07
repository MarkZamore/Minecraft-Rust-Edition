#!/usr/bin/env python3
"""Apply the LANMinecraft integration to one independently verified Horizon source tree."""

import argparse
import importlib.util
import hashlib
import json
import os
import struct
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTICE_SPEC = importlib.util.spec_from_file_location("edition_notice_overlay", HERE / "notice_overlay.py")
NOTICE_OVERLAY = importlib.util.module_from_spec(NOTICE_SPEC)
NOTICE_SPEC.loader.exec_module(NOTICE_OVERLAY)
COMMIT = "2ebf00a0281c592f79191e76fce0e7d522c6f425"
SOURCE_SHA = "90479a75b886e0b39663c1116c0424e90d0d0ea4e9633668c1c3fed09bc7d645"
SOURCE_SIZE = 47_015_509
TRANSPORT_COMMIT = "89b0ce80b31366601906e69ba5fb0c4a323ec9c6"
TRANSPORT_SHA = "f0d3f400397aaae5f56b508575019dd7fbed30e22dbf60cd6a67cd2cce505033"
STEAM_DLL_SIZE = 317_080
STEAM_DLL_SHA = "eb17909a76668cf9ae0b92a618a34a50f6c73d3a6787cb4dd8ce36a8b10bfb75"
PREFIX = f"horizon-{COMMIT[:7]}/"
CLIENT = "crates/horizon-client/"
CLIENT_LIB = "crates/horizon-client-lib/"
TRANSPORT = "crates/lanmc-steam-native/"
SOURCE_PATHS = {
    "Cargo.toml",
    "Cargo.lock",
    CLIENT_LIB + "Cargo.toml",
    CLIENT + "build.rs",
    *NOTICE_OVERLAY.SOURCES,
    *(CLIENT + "src/" + path for path in (
        "entry.rs", "lib.rs", "client_lib.rs", "game.rs", "game/no_resources.rs",
        "game/split/windows.rs", "game/integrated.rs", "game/lan.rs",
        "game/remote.rs", "game/screens.rs", "game/friends.rs", "game/dedicated.rs",
        "game/lan_screen.rs", "game/chat.rs", "lan/mod.rs",
        "lan/conn.rs", "lan/discovery.rs", "multiplayer.rs", "title.rs", "game/ui_control.rs",
    )),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def locked_inputs(archive):
    if archive.stat().st_size != SOURCE_SIZE or digest(archive) != SOURCE_SHA:
        raise ValueError("Horizon source archive does not match the reviewed Nightly")
    result = {}
    with tarfile.open(archive, "r|gz") as stream:
        for entry in stream:
            name = entry.name.removeprefix(PREFIX)
            if name not in SOURCE_PATHS:
                continue
            if not entry.isfile() or name in result:
                raise ValueError(f"Invalid source archive entry {entry.name}")
            result[name] = stream.extractfile(entry).read()
    if result.keys() != SOURCE_PATHS:
        raise ValueError("Missing locked Horizon source files")
    return result


def edit(files, path, old, new, count=1):
    old, new = old.encode("utf-8"), new.encode("utf-8")
    original = files[path]
    if original.count(old) != count:
        raise ValueError(f"Exact overlay anchor missing or ambiguous in {path}: {old[:160]!r}")
    files[path] = original.replace(old, new, count)


def changes(inputs, transport):
    patched = dict(inputs)
    patched["Cargo.lock"] = (HERE / "horizon-cargo.lock").read_bytes()

    def src(path, old, new, count=1):
        edit(patched, CLIENT + "src/" + path, old, new, count)

    edit(patched, "Cargo.toml",
         '    "crates/horizon-client",\n',
         '    "crates/horizon-client",\n    "crates/lanmc-steam-native",\n')
    edit(patched, CLIENT_LIB + "Cargo.toml",
         "[dependencies]\n",
         '[dependencies]\nlanmc_steam_native = { path = "../lanmc-steam-native" }\n')
    src("lib.rs", "pub mod entry;", "pub mod entry;\nmod lanmc_skins;\nmod lanmc_presentation;")
    edit(patched, CLIENT + "build.rs", '.join("packaging/icons/horizon.ico");',
         '.join("packaging/icons/rust-edition.ico");')
    edit(patched, CLIENT + "build.rs",
         "//! Embeds Horizon's icon (`packaging/icons/horizon.ico`) in the Windows executables, as icon",
         "//! Embeds the pack icon (`packaging/icons/rust-edition.ico`) in the Windows executables, as icon")
    for path, count in (("game.rs", 2), ("game/no_resources.rs", 1)):
        src(path, 'horizon_client::window::with_horizon_icon(\n'
            '            Window::default_attributes().with_title("Horizon"),\n        )',
            'crate::lanmc_presentation::with_icon(\n'
            '            Window::default_attributes().with_title(crate::lanmc_presentation::title("Horizon")),\n'
            '        )', count=count)
    src("game/split/windows.rs", "horizon_client::window::with_horizon_icon(Window::default_attributes())",
        "crate::lanmc_presentation::with_icon(Window::default_attributes())")
    src("game/split/windows.rs", '.with_title(format!("Horizon {key}"))',
        '.with_title(crate::lanmc_presentation::title(format!("Horizon {key}")))')
    src("game/split/windows.rs", '.with_title("Horizon")',
        '.with_title(crate::lanmc_presentation::title("Horizon"))')
    src("game/split/windows.rs", "    pub(super) fn title(&self, k: usize) -> String {",
        """    pub(super) fn title(&self, k: usize) -> String {
        if let Some(name) = crate::lanmc_presentation::name() {
            return name.to_owned();
        }""")
    # The main multiplayer page is a Steam-world view, not a filtered drawing of
    # the saved list. Hidden entries never enter its selection or ping state.
    src("multiplayer.rs", "        servers: Vec<ServerEntry>,\n",
        "        saved_view: bool,\n        selected_steam_address: Option<String>,\n        servers: Vec<ServerEntry>,\n")
    src("multiplayer.rs", '''    pub fn list(saves: &Path) -> Self {
        let servers = servers::load(&servers_file(saves));''', '''    pub fn list(saves: &Path) -> Self {
        Self::list_view(saves, false)
    }
    pub fn refresh(&mut self, saves: &Path) {
        let saved_view = matches!(self, Self::List { saved_view: true, .. });
        *self = Self::list_view(saves, saved_view);
    }
    fn list_view(saves: &Path, saved_view: bool) -> Self {
        let servers = if saved_view { servers::load(&servers_file(saves)) } else { Vec::new() };''')
    src("multiplayer.rs", '''        ping_all(&servers, &pings, &icons, &servers_file(saves));
        Self::List {
            servers,''', '''        if saved_view {
            ping_all(&servers, &pings, &icons);
        }
        Self::List {
            saved_view,
            selected_steam_address: None,
            servers,''')
    src("multiplayer.rs", "            lan: Scanner::start(),",
        "            lan: if !saved_view && crate::lanmc_steam::active() { Scanner::start() } else { Scanner::empty() },")
    src("multiplayer.rs", '''fn ping_all(servers: &[ServerEntry], pings: &Pings, icons: &Icons, file: &Path) {''',
        '''fn ping_all(servers: &[ServerEntry], pings: &Pings, icons: &Icons) {''')
    src("multiplayer.rs", "        let (pings, icons, file) = (pings.clone(), icons.clone(), file.to_owned());",
        "        let (pings, icons) = (pings.clone(), icons.clone());")
    src("multiplayer.rs", '''                            let icon = pixels.map(|_| encoded);
                            if icon != saved {
                                save_icon(&file, &address, icon);
                            }''', '''                            // Viewing a saved list never rewrites user-owned NBT.
                            // Keep status favicons in memory until the player edits or connects.
                            let _ = (pixels, encoded);''')
    src("multiplayer.rs", "const HEADER: f32 = 33.0;",
        "const VIEW_Y: f32 = 33.0;\nconst HEADER: f32 = VIEW_Y + 24.0;")
    src("multiplayer.rs", "((HEADER - 9.0) / 2.0).floor(),", "((VIEW_Y - 9.0) / 2.0).floor(),")
    src("multiplayer.rs", '''            Self::List {
                servers, selected, ..
            } => {
                let (w, h) = (w.ceil(), h.ceil());''', '''            Self::List {
                saved_view, servers, selected, selected_steam_address, lan, ..
            } => {
                let selected = selected_row_index(servers, &lan.games(), *selected, selected_steam_address.as_deref());
                let (w, h) = (w.ceil(), h.ceil());''')
    src("multiplayer.rs", '''                vec![
                    b(
                        x,
                        h - 52.0,''', '''                vec![
                    b(x, VIEW_Y, 152.0, "Миры Steam", *saved_view, 48),
                    b(x + 156.0, VIEW_Y, 152.0, "Сохранённые серверы", !*saved_view, 49),
                    b(
                        x,
                        h - 52.0,''')
    src("multiplayer.rs", '''                icons,
                lan,
                scroll,
                ..
            } => {''', '''                icons,
                lan,
                scroll,
                selected_steam_address,
                ..
            } => {''')
    src("multiplayer.rs", '''                let games = lan.games();
                let (top, bottom, left) = list_area(w, h);''', '''                let games = lan.games();
                let selected_row = selected_row_index(servers, &games, *selected, selected_steam_address.as_deref());
                let selected = &selected_row;
                let (top, bottom, left) = list_area(w, h);''')
    src("multiplayer.rs", '''                selected,
                lan,
                ..
            } => match selected.and_then(|i| entry_address(servers, lan, i)) {''', '''                selected,
                lan,
                selected_steam_address,
                ..
            } => match selected_entry_address(servers, lan, *selected, selected_steam_address.as_deref()) {''')
    src("multiplayer.rs", '''                    selected,
                    lan,
                    ..
                },
                Some(40),''', '''                    selected,
                    lan,
                    selected_steam_address,
                    ..
                },
                Some(40),''')
    src("multiplayer.rs", "                if let Some((address, lan)) = selected.and_then(|i| entry_address(servers, lan, i))",
        "                if let Some((address, lan)) = selected_entry_address(servers, lan, *selected, selected_steam_address.as_deref())")
    src("multiplayer.rs", "            (Self::List { .. }, Some(41)) => {", '''            (Self::List { .. }, Some(48)) => *self = Self::list(saves),
            (Self::List { .. }, Some(49)) => *self = Self::list_view(saves, true),
            (Self::List { .. }, Some(41)) => {''')
    src("multiplayer.rs", "            (Self::List { .. }, Some(45)) => *self = Self::list(saves),",
        "            (Self::List { .. }, Some(45)) => self.refresh(saves),")
    src("multiplayer.rs", '''                    scroll,
                    held,
                    ..
                },
                None,''', '''                    scroll,
                    held,
                    selected_steam_address,
                    ..
                },
                None,''')
    src("multiplayer.rs", '''                    Some(Row::LanHeader) => {
                        *selected = None;''', '''                    Some(Row::LanHeader) => {
                        *selected = None;
                        *selected_steam_address = None;''')
    src("multiplayer.rs", '''                    if *selected == Some(index) {
                        return Outcome::Connect { address, lan };
                    }
                    *selected = Some(index);''', '''                    if (lan && selected_steam_address.as_deref() == Some(address.as_str()))
                        || (!lan && *selected == Some(index)) {
                        return Outcome::Connect { address, lan };
                    }
                    *selected_steam_address = lan.then(|| address.clone());
                    *selected = Some(index);''')
    src("multiplayer.rs", '''                let _ = servers::save(&servers_file(saves), &list);
                *self = Self::list(saves);''', '''                let updated = (*index).filter(|&i| i < list.len()).or_else(|| list.len().checked_sub(1));
                let _ = servers::save(&servers_file(saves), &list);
                *self = Self::list_view(saves, true);
                if let Self::List { selected, .. } = self { *selected = updated; }''')
    src("multiplayer.rs", '''            (
                Self::Edit { .. }
                | Self::Direct { .. }''', '''            (Self::Edit { .. }, Some(51)) => *self = Self::list_view(saves, true),
            (
                Self::Direct { .. }''')
    src("multiplayer.rs", '''            scroll,
            ..
        } = self
        else {
            return false;
        };
        let total = servers.len() + lan.games().len();''', '''            scroll,
            selected_steam_address,
            ..
        } = self
        else {
            return false;
        };
        let games = lan.games();
        let total = servers.len() + games.len();''')
    src("multiplayer.rs", "        let next = match *selected {",
        "        let next = match selected_row_index(servers, &games, *selected, selected_steam_address.as_deref()) {")
    src("multiplayer.rs", "        *selected = next;",
        "        *selected_steam_address = next.and_then(|i| i.checked_sub(servers.len())).and_then(|i| games.get(i)).map(|g| g.address.clone());\n        *selected = next;")
    src("multiplayer.rs", '''            _ => {
                *self = Self::list(saves);
                false
            }
        }
    }
}''', '''            Self::Edit { .. } => {
                *self = Self::list_view(saves, true);
                false
            }
            _ => {
                *self = Self::list(saves);
                false
            }
        }
    }
}''')
    src("multiplayer.rs", "fn entry_address(servers: &[ServerEntry], lan: &Scanner, index: usize) -> Option<(String, bool)> {", '''// A Steam selection belongs to an address/lobby, never to a mutable discovery row.
fn selected_row_index(servers: &[ServerEntry], games: &[crate::lan::discovery::LanGame], selected: Option<usize>, steam: Option<&str>) -> Option<usize> {
    if let Some(address) = steam {
        games.iter().position(|g| g.address == address).map(|i| servers.len() + i)
    } else {
        selected.filter(|&i| i < servers.len())
    }
}
fn selected_entry_address(servers: &[ServerEntry], lan: &Scanner, selected: Option<usize>, steam: Option<&str>) -> Option<(String, bool)> {
    if let Some(address) = steam {
        lan.games().iter().any(|g| g.address == address).then(|| (address.to_owned(), true))
    } else {
        selected.and_then(|i| servers.get(i)).map(|s| (s.ip.clone(), false))
    }
}
fn entry_address(servers: &[ServerEntry], lan: &Scanner, index: usize) -> Option<(String, bool)> {''')
    src("multiplayer.rs", '''        let mut screen = Screen::List {
            servers: vec!''', '''        let mut screen = Screen::List {
            saved_view: true,
            selected_steam_address: None,
            servers: vec!''')
    src("multiplayer.rs", "        let mut screen = Screen::list(&saves);",
        "        let mut screen = Screen::list_view(&saves, true);", count=6)
    src("multiplayer.rs", '''            if let Screen::List { selected, .. } = &mut screen {
                *selected = Some(index);
            }''', '''            if let Screen::List { selected, selected_steam_address, .. } = &mut screen {
                *selected = Some(index);
                *selected_steam_address = expected_lan.then(|| address.to_owned());
            }''')
    src("lan/discovery.rs", "pub struct Scanner {\n    inner: Arc<ScanInner>,\n}",
        "pub struct Scanner {\n    inner: Arc<ScanInner>,\n    enabled: bool,\n}")
    src("lan/discovery.rs", "impl Scanner {", '''impl Scanner {
    /// A saved-server view has no automatic discovery channel.
    pub fn empty() -> Self {
        Self { enabled: false, inner: Arc::new(ScanInner {
            games: Arc::default(), stop: Arc::new(AtomicBool::new(true)), taken: Mutex::new(0),
        }) }
    }''')
    src("lan/discovery.rs", "        Self {\n            inner: Arc::new(ScanInner {",
        "        Self {\n            enabled: true,\n            inner: Arc::new(ScanInner {", count=2)
    src("lan/discovery.rs", "    pub fn take_found(&self) -> Vec<(usize, LanGame)> {",
        "    pub fn take_found(&self) -> Vec<(usize, LanGame)> {\n        if !self.enabled { return Vec::new(); }")
    src("lan/discovery.rs", "        let scanner = Scanner {\n            inner: Arc::new(ScanInner {",
        "        let scanner = Scanner {\n            enabled: true,\n            inner: Arc::new(ScanInner {")
    src("title.rs", "            *screen = crate::multiplayer::Screen::list(&self.saves);",
        "            screen.refresh(&self.saves);")
    src("title.rs", "        let outcome = screen.click(hit, x, y, size, &self.saves);",
        "        let outcome = screen.click(hit, x, y, size, &self.saves);\n        if hit.is_none() || matches!(hit, Some(48 | 49)) { self.focused_button = None; }")
    src("title.rs", "    pub fn refresh_servers(&mut self) {", '''    pub fn multiplayer_node_ids(&self, w: f32, h: f32) -> Vec<(&'static str, [f32; 4])> {
        let Mode::Multiplayer(screen @ crate::multiplayer::Screen::List { .. }) = &self.mode else { return Vec::new(); };
        screen.buttons(w, h).into_iter().filter_map(|(x, y, width, _, _, id)| {
            let name = match id {
                48 => "steam-worlds", 49 => "saved-servers",
                45 => "server-list-refresh", 46 => "server-list-back", _ => return None,
            };
            Some((name, [x, y, width, 20.0]))
        }).collect()
    }
    pub fn refresh_servers(&mut self) {''')
    src("game/ui_control.rs", "        let mut nodes = widgets::tree(&widgets::last_labelled(), &widgets::last_targets(), size);", '''        let mut nodes = widgets::tree(&widgets::last_labelled(), &widgets::last_targets(), size);
        if let (Some(title), Some([w, h])) = (&self.title, size) {
            for (id, rect) in title.multiplayer_node_ids(w, h) {
                if let Some(node) = nodes.iter_mut().find(|node| node.rect.iter().zip(rect).all(|(a, b)| (*a - b).abs() < 0.5)) {
                    node.id = id.to_owned();
                }
            }
        }''')
    src("game.rs", '''        let player_dir = horizon_client::logging::player_dir();
        if let Err(e) = horizon_client::multiplayer::add_official_server(&player_dir) {
            log::warn!("could not add the official server to servers.dat: {e}");
        }
        if let Err(e) = horizon_client::multiplayer::add_partner_servers(&player_dir) {
            log::warn!("could not add the partner servers to servers.dat: {e}");
        }
''', "")
    src("game.rs", '''    // Someone is playing: the anonymous ping behind the launcher's "N playing now", and the
    // official server in their list (servers.dat in the game folder, the working directory). Not
    // for a server, a benchmark, a run that quits by itself, a harness's fixture or a run that
    // horizon-ctl drives: those start in a source checkout and wrote the list there.
''', '''    // Keep Horizon's normal player heartbeat. The pack never seeds or migrates public
    // recommendations into the player's saved server list; Steam discovery is separate.
''')
    src("client_lib.rs", "pub mod lan;", "pub mod lan;\npub mod lanmc_steam;")
    src("entry.rs", '        crash::install(home.clone(), side);',
        '''        crash::install(home.clone(), side);
        if side == "client" {
            if let Err(error) = crate::lanmc_steam::init() {
                log::error!("LANMinecraft Steam startup failed: {error}");
                eprintln!("LANMinecraft Steam startup failed: {error}");
                std::process::exit(1);
            }
        }''')
    src("game.rs", "        split::launcher::read_now();",
        '''        if let Some(name) = crate::lanmc_steam::owner_name() {
            o.player_name = Some(name.to_owned());
        }
        split::launcher::read_now();''')
    src("lan/mod.rs",
        '''    pub listen: bool,
    /// Ops bypass a full server''',
        '''    pub listen: bool,
    /// Only the integrated world in launcher-managed Steam mode.
    pub lanmc_steam: bool,
    /// Ops bypass a full server''')
    src("lan/mod.rs", "            bind: None,\n            port,",
        "            bind: None,\n            lanmc_steam: false,\n            port,")
    src("game/dedicated.rs", "            listen: true,",
        "            listen: true,\n            lanmc_steam: false,")
    src("game/integrated.rs", "            owner: offline_uuid(&player),",
        "            owner: crate::lanmc_steam::owner_uuid(&player).unwrap_or_else(|| offline_uuid(&player)),")
    src("game/integrated.rs",
        '''        let player = auth
            .as_ref()
            .map_or_else(|| self.options.player_name(), |a| a.name.clone());''',
        '''        let player = crate::lanmc_steam::owner_name().map(str::to_owned).unwrap_or_else(|| {
            auth.as_ref().map_or_else(|| self.options.player_name(), |a| a.name.clone())
        });''')
    src("game/integrated.rs", "            online: horizon_client::lan::OnlineMode::lan(),",
        '''            lanmc_steam: crate::lanmc_steam::active(),
            online: (!crate::lanmc_steam::active())
                .then(horizon_client::lan::OnlineMode::lan).flatten(),''')
    src("game/integrated.rs",
        '''        let listener = Lan::bind(None, settings.port).map_err(|e| e.to_string())?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        log::info!("Published LAN server on port {port}");''',
        '''        let listener = Lan::bind(crate::lanmc_steam::bind_address(), settings.port)
            .map_err(|e| e.to_string())?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        crate::lanmc_steam::publish(port, &self.level.name)?;
        if crate::lanmc_steam::active() {
            log::info!("Requested Steam publication of the integrated world");
        } else {
            log::info!("Published LAN server on port {port}");
        }''')
    src("game/integrated.rs", "        s.pending = Some(Publish::Close);",
        "        crate::lanmc_steam::unpublish();\n        s.pending = Some(Publish::Close);")
    src("game/integrated.rs", "        self.native_lifecycle.begin_close();",
        "        self.native_lifecycle.begin_close();\n        crate::lanmc_steam::unpublish();")
    src("game/integrated.rs", "host.hub.lan.listen_on(listener, true)",
        "host.hub.lan.listen_on(listener, !crate::lanmc_steam::active())")
    src("game/lan.rs",
        '''        self.system_message(Text::translate(
            "menu.multiplayerOptions.publish.started.lan",
            &[Text::literal(port.to_string())],
        ));''',
        '''        if crate::lanmc_steam::active() {
            self.system_message(Text::literal(format!(
                "Локальный порт сервера: {port} (внутренний порт Steam; друзья подключаются по адресу .steam)"
            )));
        } else {
            self.system_message(Text::translate(
                "menu.multiplayerOptions.publish.started.lan",
                &[Text::literal(port.to_string())],
            ));
        }''')
    src("game/friends.rs",
        '''    pub(super) fn friends_start(&mut self) -> Result<()> {
        if self.friends.is_some() {''',
        '''    pub(super) fn friends_start(&mut self) -> Result<()> {
        if crate::lanmc_steam::active() {
            return Err(crate::lanmc_steam::steam_only_label().into());
        }
        if self.friends.is_some() {''')
    # Steam is independent of Horizon's optional `friends` feature. Keep this
    # frontend method in the unconditional Game impl, not game/friends.rs.
    src("game.rs",
        "    pub(super) fn idle(&mut self, event_loop: &dyn EventLoopCtl) {",
        """    pub(super) fn poll_steam_sharing(&mut self) {
        if let Some(message) = crate::lanmc_steam::sharing_notice() {
            self.system_message(message);
        }
        if crate::lanmc_steam::publication_failed() && self.lan_port().is_some() {
            self.lan_close(false);
        }
        if let Some(lobby) = crate::lanmc_steam::take_join_request() {
            let result = (|| -> Result<()> {
                if self.title.is_none() {
                    self.close_world()?;
                }
                let address = crate::lanmc_steam::address(lobby);
                let name = crate::lanmc_steam::owner_name().ok_or("Steam identity unavailable")?.to_owned();
                self.connect_server_with_options(&address, &name, None, true)
            })();
            if let Err(error) = result {
                log::warn!("Steam invitation could not be joined: {error}");
                self.system_message(Text::literal(error.to_string()));
            }
        }
    }

    pub(super) fn idle(&mut self, event_loop: &dyn EventLoopCtl) {
        self.poll_steam_sharing();""")
    src("game/chat.rs",
        """            Some(ClickEvent::Custom { id, payload }) => {
                if let Some(remote) = &self.remote {
                    remote.session.send_custom_click(&id, payload);
                }
            }""",
        """            Some(ClickEvent::Custom { id, payload }) => {
                if id.starts_with(crate::lanmc_steam::ACTION_PREFIX) {
                    if self.integrated.is_some() {
                        match crate::lanmc_steam::sharing_action(&id, payload.as_ref()) {
                            Some(crate::lanmc_steam::SharingAction::Invite) => {
                                if let Err(error) = crate::lanmc_steam::invite_friends() {
                                    self.system_message(Text::literal(error));
                                }
                            }
                            Some(crate::lanmc_steam::SharingAction::Close) => self.lan_close(true),
                            None => {}
                        }
                    }
                } else if let Some(remote) = &self.remote {
                    remote.session.send_custom_click(&id, payload);
                }
            }""")
    src("game/lan_screen.rs", "    pub(super) fn open_world_options(&mut self) {",
        "    #[allow(dead_code)] // The pack uses Open to LAN directly in the pause menu.\n"
        "    pub(super) fn open_world_options(&mut self) {")
    src("game/screens.rs", "/// PauseScreen buttons:",
        '''#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PauseNetwork {
    Open,
    Opening,
    Reports,
}

impl PauseNetwork {
    fn for_world(remote: bool, listening: bool, steam_published: Option<bool>) -> Self {
        if remote || steam_published == Some(true) || (listening && steam_published.is_none()) {
            Self::Reports
        } else if listening {
            Self::Opening
        } else {
            Self::Open
        }
    }
}

/// PauseScreen buttons:''')
    src("game/screens.rs",
        "    split: bool,\n) -> Vec<(f32, f32, f32, &'static str, u8, bool)> {",
        "    split: bool,\n    network: PauseNetwork,\n) -> Vec<(f32, f32, f32, &'static str, u8, bool)> {")
    src("game/screens.rs",
        '''            "options.worldOptions.button",
            9,
            true,''',
        '''            match network {
                PauseNetwork::Reports => "menu.playerReporting",
                _ => "menu.multiplayerOptions.button",
            },
            9,
            network != PauseNetwork::Opening,''')
    src("game/screens.rs", "    pub(super) fn draw_pause_menu(&mut self, batch: &mut HudBatch) {",
        '''    fn pause_network(&self) -> PauseNetwork {
        PauseNetwork::for_world(
            self.external_server(),
            self.lan_port().is_some(),
            crate::lanmc_steam::active().then(crate::lanmc_steam::is_published),
        )
    }

    fn pause_friends_available(&self) -> bool {
        !crate::lanmc_steam::active() && self.friends_view().shown
    }

    pub(super) fn draw_pause_menu(&mut self, batch: &mut HudBatch) {''')
    src("game/screens.rs", "let friends = self.friends_view().shown;",
        "let friends = self.pause_friends_available();", count=3)
    src("game/screens.rs", "(self.friends_view().shown", "(self.pause_friends_available()")
    for additions, count in (
        ("additions.is_some()", 2),
        ("self.pause_screen_additions().is_some()", 1),
    ):
        args = (
            "            self.external_server(),\n"
            "            links,\n"
            f"            {additions},\n"
            "            self.options.split_guest || self.split_hud.menu,\n"
        )
        src("game/screens.rs", args, args + "            self.pause_network(),\n", count)
    src("game/screens.rs",
        '''                    self.external_server(),
                    links,
                    self.pause_screen_additions().is_some(),
                    self.options.split_guest || self.split_hud.menu,
                );''',
        '''                    self.external_server(),
                    links,
                    self.pause_screen_additions().is_some(),
                    self.options.split_guest || self.split_hud.menu,
                    self.pause_network(),
                );''')
    src("game/screens.rs",
        '''            9 => self.open_world_options(),
            10 => self.screen = Screen::Friends,''',
        '''            9 => match self.pause_network() {
                PauseNetwork::Reports => self.open_social(true),
                PauseNetwork::Opening => {}
                PauseNetwork::Open => {
                    if let Err(error) = self.lan_open(super::lan::LanSettings {
                        port: 0,
                        guest_commands: self.integrated.as_ref().map_or_else(
                            || self.allow_commands(),
                            |server| server.call(|game| game.allow_commands()).unwrap_or(false),
                        ),
                        force_game_mode: true,
                    }) {
                        self.system_message(Text::literal(error.to_string()));
                    } else {
                        self.pause_focus = None;
                        self.screen = Screen::Playing;
                        self.capture(true);
                    }
                }
            },
            10 if self.pause_friends_available() => self.screen = Screen::Friends,''')
    src("game/screens.rs",
        "    use super::{Screen, death_buttons, pause_buttons, pause_icon_focus_ids, pause_icons};",
        '''    use super::{PauseNetwork, Screen, death_buttons, pause_icon_focus_ids, pause_icons};

    fn pause_buttons(
        w: f32, h: f32, remote: bool, links: bool, additions: bool, split: bool,
    ) -> Vec<(f32, f32, f32, &'static str, u8, bool)> {
        super::pause_buttons(w, h, remote, links, additions, split,
            PauseNetwork::for_world(remote, false, None))
    }''')
    src("game/screens.rs",
        "fn pause_menu_uses_disconnect_for_servers_and_keeps_world_options()",
        "fn pause_menu_uses_disconnect_for_servers_and_places_the_network_action()")
    src("game/screens.rs", "            assert!(buttons.iter().any(|b| b.4 == 9 && b.5));",
        '''            let network = buttons.iter().find(|b| b.4 == 9).unwrap();
            assert_eq!(network.3, if remote { "menu.playerReporting" } else { "menu.multiplayerOptions.button" });
            assert!(network.5);
            assert!(buttons.iter().all(|b| b.3 != "options.worldOptions.button"));''')
    src("game/screens.rs", "    fn pause_icons_report_bugs_and_feedback_are_active_like_vanilla() {",
        '''    fn pause_slot_waits_for_steam_and_keeps_its_position_when_reportable() {
        let slot = |listening, steam| {
            let state = PauseNetwork::for_world(false, listening, steam);
            super::pause_buttons(427.0, 240.0, false, false, false, false, state)
                .into_iter().find(|b| b.4 == 9).unwrap()
        };
        let open = slot(false, Some(false));
        let pending = slot(true, Some(false));
        let published = slot(true, Some(true));
        assert_eq!((open.3, open.5), ("menu.multiplayerOptions.button", true));
        assert_eq!((pending.3, pending.5), ("menu.multiplayerOptions.button", false));
        assert_eq!((published.3, published.5), ("menu.playerReporting", true));
        assert_eq!((open.0, open.1, open.2), (published.0, published.1, published.2));
        assert_eq!(slot(true, None).3, "menu.playerReporting");
    }

    #[test]
    fn pause_icons_report_bugs_and_feedback_are_active_like_vanilla() {''')
    src("lan/conn.rs", "    if !valid_name(&hello.name) {",
        """    let portable = if shared.config.lanmc_steam {
        if c.is_memory() {
            crate::lanmc_steam::owner_uuid(&hello.name).map(|uuid| (uuid, hello.name.clone()))
        } else {
            let Some(profile) = crate::lanmc_steam::claim_guest(address) else {
                let text = "This world requires an authenticated Steam connection";
                login_disconnect(&mut c, &translatable("disconnect.genericReason", &[text]));
                return Ok(());
            };
            Some(profile)
        }
    } else {
        None
    };
    if portable.is_none() && !valid_name(&hello.name) {""")
    src("lan/conn.rs", "    let (mut name, mut address) = (hello.name.clone(), address);",
        """    let mut name = portable.as_ref().map_or_else(|| hello.name.clone(), |(_, name)| name.clone());
    let mut address = address;""")
    src("lan/conn.rs", "    let (uuid, mut properties) = match (online, forwarding) {",
        """    let (uuid, mut properties) = if let Some((uuid, _)) = portable {
        (uuid, Vec::new())
    } else { match (online, forwarding) {""")
    src("lan/conn.rs",
        """        (None, None) => (offline_uuid(&hello.name), Vec::new()),
    };""",
        """        (None, None) => (offline_uuid(&hello.name), Vec::new()),
    }};""")
    src("lan/discovery.rs", "    games.push(LanGame { motd, address });",
        "    games.push(LanGame { motd, address, steam_host: None });")
    src("lan/discovery.rs", "    pub address: String,\n}",
        "    pub address: String,\n    pub steam_host: Option<String>,\n}")
    src("lan/discovery.rs",
        '''        if !cfg!(test)
            && !super::test_loopback()''',
        '''        if !cfg!(test)
            && !crate::lanmc_steam::active()
            && !super::test_loopback()''')
    src("lan/discovery.rs",
        '''    pub fn games(&self) -> Vec<LanGame> {
        self.inner.games.lock().unwrap().clone()
    }''',
        '''    pub fn games(&self) -> Vec<LanGame> {
        if !self.enabled { return Vec::new(); }
        if crate::lanmc_steam::active() {
            return crate::lanmc_steam::worlds().into_iter().map(|offer| LanGame {
                motd: offer.title,
                address: crate::lanmc_steam::address(offer.lobby_id),
                steam_host: Some(offer.host),
            }).collect();
        }
        self.inner.games.lock().unwrap().clone()
    }''')
    src("multiplayer.rs",
        '''                            let game = &games[k];''',
        '''                            let game = &games[k];
                            if let Some(host) = &game.steam_host {
                                batch.text(atlas, &game.motd, cx + 35.0, cy + 1.0, u32::MAX, false);
                                batch.text(atlas, host, cx + 35.0, cy + 12.0, gray, false);
                                continue;
                            }''')
    src("multiplayer.rs",
        '''                            let label = crate::lang::get_or(
                                "lanServer.scanning",
                                "Scanning for games on your local network",
                            );''',
        '''                            let label = if crate::lanmc_steam::active() {
                                crate::lanmc_steam::scan_label().to_owned()
                            } else {
                                crate::lang::get_or(
                                    "lanServer.scanning",
                                    "Scanning for games on your local network",
                                )
                            };''')
    src("multiplayer.rs", '''                                &[&lan_narration(&games[k].motd).plain()],''',
        '''                                &[&match &games[k].steam_host {
                                    Some(host) => format!("{} {host}", games[k].motd),
                                    None => lan_narration(&games[k].motd).plain(),
                                }],''')
    src("multiplayer.rs", '                motd: "LAN".into(),\n                address: address.into(),',
        '                motd: "LAN".into(),\n                address: address.into(),\n                steam_host: None,')
    src("game/remote.rs",
        '''    ) -> Result<()> {
        self.reset_tutorial();
        self.perf.world_opening();''',
        '''    ) -> Result<()> {
        let steam_endpoint = crate::lanmc_steam::join(address)?;
        let is_portable = steam_endpoint.is_some() || (local.is_some() && crate::lanmc_steam::owner_uuid(name).is_some());
        let steam_address = steam_endpoint.map(|endpoint| endpoint.to_string());
        let address = steam_address.as_deref().unwrap_or(address);
        self.reset_tutorial();
        self.perf.world_opening();''')
    src("game/remote.rs",
        '''        let auth = self.auth();
        let name = auth.as_ref().map_or(name, |a| a.name.as_str());''',
        '''        let auth = if is_portable { None } else { self.auth() };
        let name = if is_portable {
            crate::lanmc_steam::owner_name().unwrap_or(name)
        } else {
            auth.as_ref().map_or(name, |a| a.name.as_str())
        };''')
    src("game/remote.rs",
        '        let user = auth.as_ref().map_or_else(\n            || {\n                (\n                    name.to_owned(),\n                    horizon_protocol::crypto::offline_uuid(name),\n                )\n            },\n            |a| (a.name.clone(), a.uuid),\n        );\n        self.server_packs_connect(local.is_none().then_some(address), user);',
        '        let user = auth.as_ref().map_or_else(\n            || {\n                (\n                    name.to_owned(),\n                    if is_portable {\n                        crate::lanmc_steam::owner_uuid(name)\n                            .unwrap_or_else(|| horizon_protocol::crypto::offline_uuid(name))\n                    } else {\n                        horizon_protocol::crypto::offline_uuid(name)\n                    },\n                )\n            },\n            |a| (a.name.clone(), a.uuid),\n        );\n        self.server_packs_connect(local.is_none().then_some(address), user);')
    src("game/remote.rs", "pub(super) struct Remote {",
        "pub(super) struct Remote {\n    lanmc_portable: bool,")
    src("game/remote.rs",
        '        self.remote = Some(Remote {\n            session,\n            save_folder: None,\n            brand: "unknown".into(),',
        '        self.remote = Some(Remote {\n            lanmc_portable: is_portable,\n            session,\n            save_folder: None,\n            brand: "unknown".into(),')
    src("game/remote.rs",
        '        self.remote = Some(Remote {\n            session,\n            save_folder: None,\n            brand: "horizon-studio".into(),',
        '        self.remote = Some(Remote {\n            lanmc_portable: false,\n            session,\n            save_folder: None,\n            brand: "horizon-studio".into(),')
    src("game/remote.rs",
        '''        let mut capes: HashMap<u128, String> = HashMap::new();
        for (uuid, textures) in entries {''',
        '''        let mut capes: HashMap<u128, String> = HashMap::new();
        let portable_skins: Vec<_> = if r.lanmc_portable {
            r.level_players.values().copied().chain(std::iter::once(local))
                .filter_map(|uuid| crate::lanmc_skins::selected(uuid).map(|skin| (uuid, skin)))
                .collect()
        } else {
            Vec::new()
        };
        for (uuid, textures) in entries {''')
    src("game/remote.rs",
        '''        // An offline name's skin from the launcher, where neither the server''',
        '''        for (uuid, skin) in portable_skins {
            if !self.skin_slots.contains(&skin.location)
                || !self.gui.atlas.sprites.contains_key(&skin.location)
            {
                self.store_skin(skin.location.clone(), skin.rgba.as_ref().clone());
            }
            resolved.retain(|(other, _)| *other != uuid);
            slim.remove(&uuid);
            if skin.slim {
                slim.insert(uuid);
            }
            resolved.push((uuid, skin.location));
        }
        // An offline name's skin from the launcher, where neither the server''')
    for file in ("lanmc_steam.rs", "lanmc_skins.rs", "lanmc_presentation.rs"):
        patched[CLIENT + "src/" + file] = (HERE / file).read_bytes()
    artwork = artwork_inputs()
    patched[CLIENT + "src/lanmc_rust_logo.png"] = artwork["rust-logo.png"]
    patched["packaging/icons/rust-edition.ico"] = artwork["rust-edition.ico"]
    steam_files = {path.relative_to(transport).as_posix(): path.read_bytes()
                   for path in transport_inputs(transport)}
    if tree_sha(index(steam_files)) != TRANSPORT_SHA:
        raise ValueError("Shared Steam transport does not match the reviewed source pin")
    patched.update((TRANSPORT + name, data) for name, data in steam_files.items())
    NOTICE_OVERLAY.apply(patched, HERE)
    return patched


def artwork_inputs():
    folder = HERE / "rust-logo"
    lock = json.loads((folder / "artwork-lock.json").read_text(encoding="utf-8"))
    names = {"rust-logo.svg", "rust-logo.png", "rust-edition.ico"}
    if lock["files"].keys() != names:
        raise ValueError("Unexpected Rust logo asset list")
    result = {name: (folder / name).read_bytes() for name in names}
    for name, data in result.items():
        if lock["files"][name] != {"sha256": sha(data), "sizeBytes": len(data)}:
            raise ValueError(f"Rust logo file does not match its pin {name}")
    svg = result["rust-logo.svg"]
    if hashlib.sha1(f"blob {len(svg)}".encode("ascii") + bytes(1) + svg).hexdigest() != lock["gitBlob"]:
        raise ValueError("Rust logo does not match the pinned official Git blob")
    return result


def transport_inputs(transport):
    paths = [transport / "Cargo.toml", *(transport / "src").rglob("*.rs")]
    if len(paths) < 2:
        raise ValueError("The shared lanmc_steam_native crate is missing")
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Invalid shared Steam transport source {path.name}")
    return sorted(paths)


def index(files):
    return {path: sha(data) for path, data in sorted(files.items())}


def tree_sha(files):
    return sha("".join(f"{value}  {path}\n" for path, value in sorted(files.items())).encode("utf-8"))


def recipe_sha():
    return tree_sha(index({name: (HERE / name).read_bytes() for name in (
        Path(__file__).name, "lanmc_steam.rs", "lanmc_skins.rs", "lanmc_presentation.rs",
        "notice_overlay.py", *NOTICE_OVERLAY.COPY_FILES,
        *(f"notice-platform/{name}" for name in NOTICE_OVERLAY.PLATFORM_FILES),
        "horizon-cargo.lock", "rust-logo/artwork-lock.json", "rust-logo/render.py",
        "rust-logo/NOTICE.txt", "rust-logo/rust-logo.svg", "rust-logo/rust-logo.png",
        "rust-logo/rust-edition.ico",
    )}))


def staged_path(source, name):
    path = source / name
    if path.resolve() != path.absolute():
        raise ValueError(f"Linked source path {name}")
    return path


def apply_or_validate(source, originals, patched, apply):
    for name, output in patched.items():
        path = staged_path(source, name)
        expected = originals.get(name) if apply else output
        if expected is None:
            if path.exists():
                raise ValueError(f"Unexpected source file already present {name}")
        elif not path.is_file() or path.read_bytes() != expected:
            raise ValueError(f"Source tree does not match the expected bytes {name}")
    if apply:
        for name, data in patched.items():
            path = staged_path(source, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def verify_build_tree(source, archive, patched):
    expected = set(patched)
    with tarfile.open(archive, "r|gz") as stream:
        for entry in stream:
            if entry.isdir():
                continue
            name = entry.name.removeprefix(PREFIX)
            if name == "CLAUDE.md" and entry.issym() and entry.linkname == "AGENTS.md":
                # Windows' tar extraction materializes this author docs link as a copy.
                alias = staged_path(source, name)
                if alias.read_bytes() != staged_path(source, "AGENTS.md").read_bytes():
                    raise ValueError("The source documentation alias differs from its target")
                expected.add(name)
                continue
            if not entry.isfile() or not entry.name.startswith(PREFIX):
                raise ValueError(f"Unexpected source archive entry {entry.name}")
            if name not in patched:
                path = staged_path(source, name)
                if path.stat().st_size != entry.size or digest(path) != hashlib.file_digest(
                    stream.extractfile(entry), "sha256"
                ).hexdigest():
                    raise ValueError(f"Unexpected source change outside the overlay {name}")
            expected.add(name)
    actual = {path.relative_to(source).as_posix()
              for path in source.rglob("*") if path.is_file() or path.is_symlink()}
    if actual != expected:
        unexpected = ", ".join(sorted(actual - expected)[:5])
        missing = ", ".join(sorted(expected - actual)[:5])
        raise ValueError(f"Source file set changed (extra: {unexpected}; missing: {missing})")


def verify_pe_icon(client, ico):
    with client.open("rb") as file:
        file.seek(60)
        pe = struct.unpack("<I", file.read(4))[0]
        file.seek(pe + 4)
        coff = file.read(20)
        section_count = struct.unpack_from("<H", coff, 2)[0]
        optional_size = struct.unpack_from("<H", coff, 16)[0]
        file.seek(pe + 24 + optional_size)
        sections = [file.read(40) for _ in range(section_count)]
        resources = [section for section in sections if section[:8].rstrip(bytes(1)) == b".rsrc"]
        if len(resources) != 1:
            raise ValueError("The client has no unambiguous Windows resource section")
        size, offset = struct.unpack_from("<II", resources[0], 16)
        if not size or size > 16 * 1024 * 1024 or offset + size > client.stat().st_size:
            raise ValueError("The client has an invalid Windows resource section")
        file.seek(offset)
        data = file.read(size)
    count = struct.unpack_from("<H", ico, 4)[0]
    for index in range(count):
        size, offset = struct.unpack_from("<II", ico, 6 + index * 16 + 8)
        if ico[offset:offset + size] not in data:
            raise ValueError("A pinned Rust logo frame is missing from the Windows PE icon")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--steam-transport", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--client", type=Path)
    parser.add_argument("--steam-dll", type=Path)
    args = parser.parse_args()
    if args.check and (args.source or args.receipt or args.client or args.steam_dll):
        parser.error("--check computes overlay output without a staged checkout")
    if not args.check and args.source is None:
        parser.error("--source is required to apply the overlay or verify a completed build")
    if (args.receipt is not None) != (args.client is not None and args.steam_dll is not None):
        parser.error("--receipt requires both --client and --steam-dll")
    original = locked_inputs(args.source_archive)
    patched = changes(original, args.steam_transport.resolve())
    if args.source is not None:
        apply_or_validate(args.source.resolve(strict=True), original, patched, args.receipt is None)
    evidence = {
        "schemaVersion": 1,
        "upstreamCommit": COMMIT,
        "upstreamSourceSha256": SOURCE_SHA,
        "recipeSha256": recipe_sha(),
        "steamTransportCommit": TRANSPORT_COMMIT,
        "steamTransportSourceSha256": TRANSPORT_SHA,
        "patchedFiles": index(patched),
    }
    if args.receipt:
        verify_build_tree(args.source.resolve(strict=True), args.source_archive, patched)
        for path, name, prefix in (
            (args.client, "horizon-client.exe", "client"),
            (args.steam_dll, "steam_api64.dll", "steamDll"),
        ):
            if path.name.lower() != name or path.is_symlink():
                raise ValueError(f"Expected regular binary {name}")
            with path.open("rb") as file:
                header = file.read(64)
                if len(header) != 64 or not header.startswith(b"MZ"):
                    raise ValueError(f"Expected PE binary {name}")
                file.seek(struct.unpack_from("<I", header, 60)[0])
                if file.read(6) != struct.pack("<4sH", b"PE" + bytes(2), 0x8664):
                    raise ValueError(f"Expected x64 PE binary {name}")
            evidence[prefix + "Sha256"] = digest(path)
            evidence[prefix + "SizeBytes"] = path.stat().st_size
        if (evidence["steamDllSizeBytes"], evidence["steamDllSha256"]) != (STEAM_DLL_SIZE, STEAM_DLL_SHA):
            raise ValueError("Steam DLL does not match the reviewed Valve redistributable")
        verify_pe_icon(args.client, patched["packaging/icons/rust-edition.ico"])
        receipt = args.receipt.absolute()
        temp = receipt.with_suffix(receipt.suffix + ".tmp")
        with temp.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(evidence, file, indent=2, ensure_ascii=True)
            file.write("\n")
        os.replace(temp, receipt)
    print(json.dumps(evidence, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, tarfile.TarError) as error:
        print(f"Horizon overlay: {error}", file=sys.stderr)
        sys.exit(1)
