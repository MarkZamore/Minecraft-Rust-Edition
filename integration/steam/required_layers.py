"""Reviewed source postconditions for Edition's mandatory Horizon integration.

This is an independent drift guard, not a Rust parser or runtime proof. Exact
archive/recipe verification and exact-client acceptance remain required. A new
upstream API may need an explicitly reviewed equivalent here; deleting a failing
condition is not an automatic resolution of a mandatory-layer conflict.
"""

import re


CLIENT = "crates/horizon-client/"
SRC = CLIENT + "src/"
NATIVE = "crates/lanmc-steam-native/src/"
RENDER = "crates/horizon-client-render/src/"
NOTICE = RENDER + "gfx/"

# The fragments include call/guard context rather than names alone. Whitespace
# formatting is ignored, but changed behavior or a removed hook blocks the gate.
LAYERS = {
    "steam-startup-identity": (
        (SRC + "entry.rs", "if let Err(error) = crate::lanmc_steam::init() {"),
        (SRC + "entry.rs", "std::process::exit(1);"),
        (SRC + "game.rs", "if let Some(name) = crate::lanmc_steam::owner_name() { o.player_name = Some(name.to_owned()); }"),
        ("crates/horizon-client-lib/Cargo.toml", 'lanmc_steam_native = { path = "../lanmc-steam-native" }'),
        (SRC + "client_lib.rs", "pub mod lanmc_steam;"),
    ),
    "steam-authenticated-profile": (
        (SRC + "game/integrated.rs", "owner: crate::lanmc_steam::owner_uuid(&player).unwrap_or_else(|| offline_uuid(&player)),"),
        (SRC + "lan/conn.rs", "let portable = if shared.config.lanmc_steam {"),
        (SRC + "lan/conn.rs", "let Some(profile) = crate::lanmc_steam::claim_guest(address) else {"),
        (SRC + "lan/conn.rs", 'let text = "This world requires an authenticated Steam connection"; login_disconnect(&mut c, &translatable("disconnect.genericReason", &[text])); return Ok(());'),
        (SRC + "game/remote.rs", "let auth = if is_portable { None } else { self.auth() };"),
    ),
    "steam-publish-close-loopback": (
        (SRC + "game/integrated.rs", "Lan::bind(crate::lanmc_steam::bind_address(), settings.port)"),
        (SRC + "game/integrated.rs", "crate::lanmc_steam::publish(port, &self.level.name)?;"),
        (SRC + "game/integrated.rs", "crate::lanmc_steam::unpublish(); s.pending = Some(Publish::Close);"),
        (SRC + "game/integrated.rs", "self.native_lifecycle.begin_close(); crate::lanmc_steam::unpublish();"),
        (SRC + "game/integrated.rs", "host.hub.lan.listen_on(listener, !crate::lanmc_steam::active())"),
        (SRC + "game/lan.rs", 'if crate::lanmc_steam::active() { self.system_message(Text::literal(format!('),
        (SRC + "game/lan.rs", 'Локальный порт сервера: {port} (внутренний порт Steam; друзья подключаются по адресу .steam)'),
    ),
    "steam-frontend-invitations": (
        (SRC + "game.rs", "pub(super) fn idle(&mut self, event_loop: &dyn EventLoopCtl) { self.poll_steam_sharing();"),
        (SRC + "game/friends.rs", "self.system_message(message);"),
        (SRC + "game/friends.rs", "if let Some(lobby) = crate::lanmc_steam::take_join_request() {"),
        (SRC + "game/friends.rs", "if self.title.is_none() { self.close_world()?; }"),
        (SRC + "game/friends.rs", "self.connect_server_with_options(&address, &name, None, true)"),
        (SRC + "game/remote.rs", "let steam_endpoint = crate::lanmc_steam::join(address)?;"),
        (NATIVE + "worker.rs", "steam.register_callback(move |invite: GameLobbyJoinRequested| {"),
        (NATIVE + "worker.rs", "steam.register_callback(move |invite: GameRichPresenceJoinRequested| {"),
        (NATIVE + "worker.rs", "else if let Some(owner) = self.invited_world_owner(lobby, friend) {"),
        (NATIVE + "worker.rs", ".authorize(lobby.raw(), owner.raw(), Instant::now());"),
        (NATIVE + "worker.rs", ".take_owner(lobby_id, Instant::now(), HANDSHAKE)"),
        (NATIVE + "worker.rs", ".or_else(|| self.discovered.get(&lobby).copied()) .filter(|owner| self.world_offer(lobby, *owner).is_some())"),
        (NATIVE + "worker.rs", ".fresh_lobbies .get(&lobby) .is_some_and(|time| time.elapsed() <= DISCOVERY_TTL)"),
        (NATIVE + "worker.rs", ".get_friend(inviter) .has_friend(FriendFlags::IMMEDIATE)"),
        (NATIVE + "worker.rs", "self.world_offer(lobby, owner).map(|_| owner)"),
        (NATIVE + "lib.rs", "mod invitation;"),
        (NATIVE + "invitation.rs", "let (authorized_lobby, owner, since) = self.authorized.take()?;"),
        (NATIVE + "invitation.rs", "(authorized_lobby == lobby && now.checked_duration_since(since)? <= max_age) .then_some(owner)"),
    ),
    "pause-and-host-command-permission": (
        (SRC + "game/screens.rs", 'PauseNetwork::Reports => "menu.playerReporting",'),
        (SRC + "game/screens.rs", "guest_commands: self.integrated.as_ref().map_or_else( || self.allow_commands(), |server| server.call(|game| game.allow_commands()).unwrap_or(false), ),"),
        (SRC + "game/screens.rs", "self.pause_focus = None; self.screen = Screen::Playing; self.capture(true);"),
    ),
    "current-host-chat-actions": (
        (SRC + "game/chat.rs", "if id.starts_with(crate::lanmc_steam::ACTION_PREFIX) { if self.integrated.is_some() {"),
        (SRC + "game/chat.rs", "Some(crate::lanmc_steam::SharingAction::Close) => self.lan_close(true),"),
        (SRC + "lanmc_steam.rs", "Text::copyable(address(lobby))"),
        (SRC + "lanmc_steam.rs", '"lanminecraft.steam.invite"'),
        (SRC + "lanmc_steam.rs", '"lanminecraft.steam.close"'),
        (SRC + "lanmc_steam.rs", "if current != Some(lobby) { return None; }"),
    ),
    "steam-world-list": (
        (SRC + "lan/discovery.rs", "&& !crate::lanmc_steam::active()"),
        (SRC + "lan/discovery.rs", "return crate::lanmc_steam::worlds().into_iter().map(|offer| LanGame { motd: offer.title, address: crate::lanmc_steam::address(offer.lobby_id), steam_host: Some(offer.host), }).collect();"),
        (SRC + "multiplayer.rs", "batch.text(atlas, &game.motd, cx + 35.0, cy + 1.0, u32::MAX, false);"),
        (SRC + "multiplayer.rs", "batch.text(atlas, host, cx + 35.0, cy + 12.0, gray, false);"),
    ),
    "steam-peer-owner-and-membership": (
        (NATIVE + "worker.rs", "if matchmaking.lobby_owner(lobby) != owner || !matchmaking.lobby_members(lobby).contains(&self.local_id) || self.world_offer(lobby, owner).is_none()"),
        (NATIVE + "worker.rs", "matchmaking.lobby_data(lobby, VARIANT_KEY).as_deref() != Some(self.variant.as_str())"),
        (NATIVE + "worker.rs", "&& members.contains(&peer) && friends.get_friend(peer).has_friend(FriendFlags::IMMEDIATE)"),
    ),
    "sharing-cancellation-and-ready-claim": (
        (NATIVE + "lib.rs", "self.control.sharing_epoch.fetch_add(1, Ordering::AcqRel); shared.claims.clear(); shared.published = false; shared.published_lobby = None;"),
        (NATIVE + "worker.rs", "self.sync_sharing(); if epoch != self.observed_epoch { return Ok(()); }"),
        (NATIVE + "worker.rs", "shared.lock().unwrap().claims.insert(address, identity); if let Err(error) = ready() { shared.lock().unwrap().claims.remove(&address); return Err(error); }"),
        (NATIVE + "worker.rs", "std::backtrace::Backtrace::force_capture()"),
    ),
    "portable-skins": (
        (SRC + "lib.rs", "mod lanmc_skins;"),
        (SRC + "game/remote.rs", "crate::lanmc_skins::selected(uuid).map(|skin| (uuid, skin))"),
        (SRC + "lanmc_skins.rs", 'std::env::var_os("LANMINECRAFT_SKIN_REGISTRY")?'),
        (SRC + "lanmc_skins.rs", 'format!("{:x}", Sha256::digest(&png)) != profile.hash'),
        (SRC + "lanmc_skins.rs", "crate::skins::process_legacy_skin(width, height, &rgba)"),
    ),
    "pack-window-title-and-icon": (
        (SRC + "lib.rs", "mod lanmc_presentation;"),
        (SRC + "game.rs", 'crate::lanmc_presentation::title("Horizon")'),
        (SRC + "game.rs", "crate::lanmc_presentation::with_icon("),
        (SRC + "game/no_resources.rs", 'crate::lanmc_presentation::title("Horizon")'),
        (SRC + "game/no_resources.rs", "crate::lanmc_presentation::with_icon("),
        (SRC + "game/split/windows.rs", "crate::lanmc_presentation::with_icon(Window::default_attributes())"),
        (CLIENT + "build.rs", '.join("packaging/icons/rust-edition.ico")'),
        (SRC + "lanmc_presentation.rs", 'std::env::var("LANMINECRAFT_BUILD_NAME")'),
        (SRC + "lanmc_presentation.rs", 'include_bytes!("lanmc_rust_logo.png")'),
        (SRC + "lanmc_presentation.rs", "let attributes = attributes.with_window_icon(icon.clone());"),
        (SRC + "lanmc_presentation.rs", "winit::platform::windows::WindowAttributesExtWindows::with_taskbar_icon(attributes, icon)"),
    ),
    "native-launcher-notices": (
        (RENDER + "gfx.rs", "mod lanmc_notices;"),
        (RENDER + "gfx.rs", "lanmc_notices: lanmc_notices::Overlay::new(),"),
        (RENDER + "gfx.rs", "if self.surface.is_some() { self.lanmc_notices.draw( &self.device, &self.queue, encoder, view_target, [self.config.width, self.config.height], self.frame_format, ); } if overlay.real { self.own_timing = timing; } readback"),
        (RENDER + "gfx.rs", "after_present: self.lanmc_notices.callback(),"),
        (RENDER + "gfx.rs", "Err(back) => (back.encoder, back.surface, back.after, back.after_present),"),
        (RENDER + "gfx.rs", "self.queue.present(surface_frame); if let Some(callback) = after_present { callback(); }"),
        (RENDER + "gfx.rs", "self.frame_gen.present(real, generated, fg_made, after_present);"),
        (RENDER + "gfx.rs", "let after_present = if frame_gen::generated_only() { notice_generated_callback } else { after_present }; self.frame_gen.present(real, generated, fg_made, after_present);"),
        (RENDER + "gfx.rs", "self.draw_overlay(&mut encoder, frame, &generated_view, generated, &overlay); notice_generated_callback = self.lanmc_notices.callback();"),
        (RENDER + "gfx.rs", "else if frame_gen::generated_only() && self.surface.is_some() { self.lanmc_notices.draw( &self.device, &self.queue, &mut encoder, &generated_view, [self.config.width, self.config.height], self.frame_format, ); notice_generated_callback = self.lanmc_notices.callback(); }"),
        (NOTICE + "compositor.rs", "self.lanmc_notices.draw( &self.device, &self.queue, &mut encoder, &target, [self.config.width, self.config.height], self.config.format, ); let after_present = self.lanmc_notices.callback(); self.queue.submit([encoder.finish()]); self.queue.present(frame); if let Some(callback) = after_present { callback(); } true"),
        (NOTICE + "submit.rs", "pub after_present: Option<AfterSubmit>,"),
        (NOTICE + "submit.rs", "if let Some(surface) = job.surface { queue.present(surface); if let Some(callback) = job.after_present { callback(); } }"),
        (NOTICE + "frame_gen.rs", "after_present: Option<Box<dyn FnOnce() + Send>>,"),
        (NOTICE + "frame_gen.rs", "if failed_here.load(Ordering::Relaxed) { return false; }"),
        (NOTICE + "frame_gen.rs", "queue.present(target); true"),
        (NOTICE + "frame_gen.rs", "if show(&pair.second) { if let Some(callback) = pair.after_present { callback(); } }"),
        (NOTICE + "lanmc_notices.rs", 'mod protocol;'),
        (NOTICE + "lanmc_notices.rs", "mod raster;"),
        (NOTICE + "lanmc_notices.rs", "mod render;"),
        (NOTICE + "lanmc_notices.rs", "mod state;"),
        (NOTICE + "lanmc_notices.rs", "self.encoded_count = Some(0);"),
        (NOTICE + "lanmc_notices.rs", "Some(Box::new(move || service.report_presented(count)))"),
        (NOTICE + "lanmc_notice_state.rs", 'std::env::var_os("LANMINECRAFT_NOTICE_DIRECTORY")?'),
        (NOTICE + "lanmc_notice_state.rs", "if !directory.is_absolute() || !ordinary_directory(&directory) { return None; }"),
        (NOTICE + "lanmc_notice_state.rs", "tick = !tick; if tick { let _ = worker.heartbeat(); } thread::sleep(Duration::from_millis(250));"),
        (NOTICE + "lanmc_notice_state.rs", "frame.timestamp_ms = unix_ms(); frame.rendered_count = rendered_count.min(3);"),
        (NOTICE + "lanmc_notice_state.rs", "let first = (read_document(&style)?, read_document(&notices)?); let second = (read_document(&style)?, read_document(&notices)?); if first != second {"),
        (NOTICE + "lanmc_notice_state.rs", '"backend=native-ui-v1\\npid={}\\ntimestampUnixMs={}\\nrenderedCount={}\\ntransformations=0\\nerror={}\\n"'),
        (NOTICE + "lanmc_notice_state.rs", "std::process::id(), frame.timestamp_ms, frame.rendered_count.min(3),"),
        (NOTICE + "lanmc_notice_state.rs", 'let target = directory.join("heartbeat.properties");'),
        (NOTICE + "lanmc_notice_state.rs", ".create_new(true) .write(true) .open(&temporary)"),
        (NOTICE + "lanmc_notice_state.rs", 'fs::rename(&temporary, &target).map_err(|_| "Notice heartbeat replace failed")?;'),
        (NOTICE + "lanmc_notice_protocol.rs", "pub const MAX_BYTES: usize = 32 * 1024;"),
        (NOTICE + "lanmc_notice_protocol.rs", "pub const LIFE_MS: u64 = 7_000;"),
        (NOTICE + "lanmc_notice_protocol.rs", "if title.encode_utf16().count() > 160 || body.encode_utf16().count() > 320 {"),
        (NOTICE + "lanmc_notice_protocol.rs", "if created_unix_ms > now_ms.saturating_add(2_000) {"),
        (NOTICE + "lanmc_notice_raster.rs", "let dpi = snapshot.dpi_scale as f64;"),
        (NOTICE + "lanmc_notice_raster.rs", "let width = (s.width as f64 * dpi)"),
        (NOTICE + "lanmc_notice_raster.rs", "CreateFontW( -size,"),
        (NOTICE + "lanmc_notice_raster.rs", "DrawTextW( self.dc, text.as_ptr(), text.len() as i32,"),
        (NOTICE + "lanmc_notice_render.rs", "self.uploads.texture( encoder, texture.as_image_copy(), card.width, card.height, card.rgba, );"),
        (NOTICE + "lanmc_notice_render.rs", 'label: Some("LANMinecraft final notices"),'),
        (NOTICE + "lanmc_notice_render.rs", "self.uploads.finish(encoder);"),
        (NOTICE + "lanmc_notice_render.rs", "pass.draw(first..first + 6, 0..1);"),
        (NOTICE + "lanmc_notice_layout.rs", "width: card.width as f32, height: card.height as f32,"),
        (NOTICE + "lanmc_notice_layout.rs", "if age >= motion.life_ms { continue; }"),
    ),
}

ASSETS = {
    SRC + "lanmc_rust_logo.png": b"\x89PNG\r\n\x1a\n",
    "packaging/icons/rust-edition.ico": b"\x00\x00\x01\x00",
}
REQUIRED_LAYER_IDS = tuple(LAYERS)
REQUIRED_PATCH_FILES = frozenset(path for rules in LAYERS.values() for path, _ in rules) | frozenset(ASSETS)
RAW_STRING_START = re.compile(rb'r(#{0,255})"')


def _compact(value):
    return b" ".join(value.split())


def _rust_without_comments(source):
    """Ignore commented-out hooks, preserving normal/raw string contents."""
    result = bytearray()
    at = 0
    while at < len(source):
        if source.startswith(b"//", at):
            end = source.find(b"\n", at)
            at = len(source) if end < 0 else end
            result.extend(b" ")
        elif source.startswith(b"/*", at):
            depth = 1
            at += 2
            while at < len(source) and depth:
                if source.startswith(b"/*", at):
                    depth += 1
                    at += 2
                elif source.startswith(b"*/", at):
                    depth -= 1
                    at += 2
                else:
                    at += 1
            if depth:
                raise ValueError("Unterminated Rust comment in mandatory-layer source")
            result.extend(b" ")
        elif match := RAW_STRING_START.match(source, at):
            end = source.find(b'"' + match[1], at + len(match[0]))
            if end < 0:
                raise ValueError("Unterminated Rust raw string in mandatory-layer source")
            end += 1 + len(match[1])
            result.extend(source[at:end])
            at = end
        elif source[at] == ord('"'):
            start = at
            at += 1
            while at < len(source):
                if source[at] == ord("\\"):
                    at += 2
                elif source[at] == ord('"'):
                    at += 1
                    break
                else:
                    at += 1
            result.extend(source[start:at])
        else:
            result.append(source[at])
            at += 1
    return bytes(result)


def validate_patched_sources(patched):
    """Reject missing mandatory source outcomes before accepting a built tree."""
    sources = {
        path: _compact(_rust_without_comments(data) if path.endswith(".rs") else data)
        for path in REQUIRED_PATCH_FILES
        if isinstance(data := patched.get(path), bytes) and path not in ASSETS
    }
    for layer, rules in LAYERS.items():
        for path, fragment in rules:
            if _compact(fragment.encode("utf-8")) not in sources.get(path, b""):
                raise ValueError(f"Mandatory layer {layer} is missing its reviewed outcome in {path}: {fragment[:90]}")
    game = sources[SRC + "game.rs"]
    for call in (b"add_official_server(", b"add_partner_servers("):
        if call in game:
            raise ValueError("Mandatory layer steam-world-list forbids automatic public server seeding")
    # Every presenter mode retains its callback. One surviving envelope is not
    # enough when the generated-frame presenter has three submission branches.
    if sources[NOTICE + "frame_gen.rs"].count(b"interval: self.interval, after_present,") < 3:
        raise ValueError("Mandatory layer native-launcher-notices lost a frame-generation presentation callback")
    for path, magic in ASSETS.items():
        data = patched.get(path)
        if not isinstance(data, bytes) or not data.startswith(magic) or len(data) <= len(magic):
            raise ValueError(f"Mandatory pack artwork is absent or invalid: {path}")
    return REQUIRED_LAYER_IDS


def validate_receipt_coverage(receipt):
    """Check structural coverage only; hashes/recipe/runtime QA are separate gates.

    Historical valid receipts need no new fields. File presence cannot prove a
    hook survived: run validate_patched_sources on recomputed producer output too.
    """
    files = receipt.get("patchedFiles") if isinstance(receipt, dict) else None
    if not isinstance(files, dict):
        raise ValueError("Overlay receipt has no patchedFiles coverage")
    for path in sorted(REQUIRED_PATCH_FILES):
        digest = files.get(path)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Overlay receipt lacks valid mandatory-file coverage: {path}")
    return REQUIRED_LAYER_IDS
