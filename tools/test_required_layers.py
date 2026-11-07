"""Fault injection for mandatory-layer drift guards, without a game or downloads.

The fixture reads the real recipe's replacement templates and bundled modules,
not the guard's fragments. A small reviewed native-transport fixture keeps these
tests portable; staging must additionally check the complete pinned output tree.
"""

import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STEAM = ROOT / "integration/steam"
SPEC = importlib.util.spec_from_file_location("edition_required_layers", STEAM / "required_layers.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def literal(node, values):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(literal(item, values) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return literal(node.left, values) + literal(node.right, values)
    if isinstance(node, ast.JoinedStr):
        return "".join(str(literal(item.value, values)) if isinstance(item, ast.FormattedValue)
                       else item.value for item in node.values)
    raise ValueError("Not a static replacement template")


def recipe_fixture():
    files = {}
    tree = ast.parse((STEAM / "apply_horizon_overlay.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "changes")

    def walk(statements, values, notice=False):
        for node in statements:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    values[node.targets[0].id] = literal(node.value, values)
                except (KeyError, TypeError, ValueError):
                    pass
            elif isinstance(node, ast.For):
                try:
                    items = literal(node.iter, values)
                except (KeyError, TypeError, ValueError):
                    continue
                targets = node.target.elts if isinstance(node.target, ast.Tuple) else [node.target]
                for item in items:
                    local = dict(values)
                    bindings = item if isinstance(node.target, ast.Tuple) else [item]
                    local.update((target.id, value) for target, value in zip(targets, bindings))
                    walk(node.body, local, notice)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if not isinstance(call.func, ast.Name):
                    continue
                try:
                    if call.func.id == "src":
                        path = "crates/horizon-client/src/" + literal(call.args[0], values)
                        replacement = literal(call.args[2], values)
                    elif call.func.id == "edit":
                        if notice:
                            path = "crates/horizon-client-render/src/" + literal(call.args[0], values)
                            replacement = literal(call.args[2], values)
                        else:
                            path = literal(call.args[1], values)
                            replacement = literal(call.args[3], values)
                    elif call.func.id == "replace" and notice:
                        path = literal(call.args[0], values)
                        replacement = literal(call.args[2], values)
                    else:
                        continue
                    count = next((literal(keyword.value, values) for keyword in call.keywords
                                  if keyword.arg == "count"), 1)
                    files[path] = files.get(path, b"") + (replacement.encode("utf-8") + b"\n") * count
                except (KeyError, TypeError, ValueError):
                    continue

    walk(function.body, {"CLIENT": "crates/horizon-client/", "CLIENT_LIB": "crates/horizon-client-lib/"})
    notice_tree = ast.parse((STEAM / "notice_overlay.py").read_text(encoding="utf-8"))
    notice_apply = next(node for node in notice_tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "apply")
    notice_globals = {}
    for node in notice_tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                notice_globals[node.targets[0].id] = literal(node.value, notice_globals)
            except (KeyError, TypeError, ValueError):
                pass
    walk(notice_apply.body, notice_globals, notice=True)
    for name in ["lanmc_notices.rs", "lanmc_notice_protocol.rs", "lanmc_notice_state.rs",
                 "lanmc_notice_raster.rs", "lanmc_notice_render.rs", "lanmc_notice_layout.rs"]:
        files["crates/horizon-client-render/src/gfx/" + name] = (STEAM / name).read_bytes()
    for name in ["Cargo.toml", "src/lib.rs"]:
        files["crates/lanmc-notice-platform/" + name] = (STEAM / "notice-platform" / name).read_bytes()
    for name in ["lanmc_steam.rs", "lanmc_skins.rs", "lanmc_presentation.rs"]:
        files["crates/horizon-client/src/" + name] = (STEAM / name).read_bytes()
    files["crates/horizon-client/src/lanmc_rust_logo.png"] = (STEAM / "rust-logo/rust-logo.png").read_bytes()
    files["packaging/icons/rust-edition.ico"] = (STEAM / "rust-logo/rust-edition.ico").read_bytes()
    files["crates/lanmc-steam-native/src/lib.rs"] = b"""
        mod invitation;
        self.control.sharing_epoch.fetch_add(1, Ordering::AcqRel);
        shared.claims.clear(); shared.published = false; shared.published_lobby = None;
    """
    files["crates/lanmc-steam-native/src/worker.rs"] = b"""
        steam.register_callback(move |invite: GameLobbyJoinRequested| { });
        steam.register_callback(move |invite: GameRichPresenceJoinRequested| { });
        else if let Some(owner) = self.invited_world_owner(lobby, friend) {
            self.invited_join.authorize(lobby.raw(), owner.raw(), Instant::now());
        }
        self.invited_join.take_owner(lobby_id, Instant::now(), HANDSHAKE);
        invited_owner.or_else(|| self.discovered.get(&lobby).copied())
            .filter(|owner| self.world_offer(lobby, *owner).is_some());
        self.fresh_lobbies .get(&lobby) .is_some_and(|time| time.elapsed() <= DISCOVERY_TTL);
        self.steam.friends().get_friend(inviter) .has_friend(FriendFlags::IMMEDIATE);
        self.world_offer(lobby, owner).map(|_| owner);
        if matchmaking.lobby_owner(lobby) != owner
          || !matchmaking.lobby_members(lobby).contains(&self.local_id)
          || self.world_offer(lobby, owner).is_none() { return Err(error); }
        matchmaking.lobby_data(lobby, VARIANT_KEY).as_deref() != Some(self.variant.as_str());
        valid_steam_id(peer.raw()) && members.contains(&peer)
          && friends.get_friend(peer).has_friend(FriendFlags::IMMEDIATE);
        self.sync_sharing(); if epoch != self.observed_epoch { return Ok(()); }
        shared.lock().unwrap().claims.insert(address, identity);
        if let Err(error) = ready() {
          shared.lock().unwrap().claims.remove(&address); return Err(error);
        }
        std::backtrace::Backtrace::force_capture();
    """
    files["crates/lanmc-steam-native/src/invitation.rs"] = b"""
        let (authorized_lobby, owner, since) = self.authorized.take()?;
        (authorized_lobby == lobby && now.checked_duration_since(since)? <= max_age)
            .then_some(owner)
    """
    return files


class RequiredLayersTests(unittest.TestCase):
    def setUp(self):
        self.patched = recipe_fixture()
        self.receipt = {"patchedFiles": {name: hashlib.sha256(data).hexdigest()
                                         for name, data in self.patched.items()}}

    def lose(self, path, old, new=b""):
        self.assertIn(old, self.patched[path])
        self.patched[path] = self.patched[path].replace(old, new)

    def test_current_recipe_templates_and_bundled_modules_cover_reviewed_layers(self):
        self.assertEqual(set(guard.validate_patched_sources(self.patched)), {
            "steam-startup-identity", "steam-authenticated-profile", "steam-publish-close-loopback",
            "steam-frontend-invitations", "pause-and-host-command-permission", "current-host-chat-actions",
            "steam-world-list", "steam-peer-owner-and-membership", "sharing-cancellation-and-ready-claim",
            "portable-skins", "pack-window-title-and-icon", "native-launcher-notices",
        })
        guard.validate_receipt_coverage(self.receipt)

    def test_removed_whole_mandatory_file_or_artwork_is_rejected(self):
        for path in ["crates/horizon-client/src/game/chat.rs", "crates/horizon-client/src/game/screens.rs",
                     "crates/horizon-client/src/lan/conn.rs", "crates/lanmc-steam-native/src/worker.rs",
                     "crates/horizon-client/src/lanmc_skins.rs", "crates/horizon-client/src/game/no_resources.rs",
                     "packaging/icons/rust-edition.ico"]:
            with self.subTest(path=path):
                broken = dict(self.patched)
                del broken[path]
                with self.assertRaises(ValueError):
                    guard.validate_patched_sources(broken)

    def test_vanilla_file_still_listed_does_not_count_as_steam_auth(self):
        self.patched["crates/horizon-client/src/lan/conn.rs"] = b"if !valid_name(&hello.name) { return Ok(()); }"
        # Coverage can remain valid, so source outcomes must be checked separately.
        guard.validate_receipt_coverage(self.receipt)
        with self.assertRaisesRegex(ValueError, "steam-authenticated-profile"):
            guard.validate_patched_sources(self.patched)

    def test_permission_regression_to_hardcoded_false_is_rejected(self):
        self.lose("crates/horizon-client/src/game/screens.rs",
                  b"server.call(|game| game.allow_commands()).unwrap_or(false)", b"false")
        with self.assertRaisesRegex(ValueError, "pause-and-host-command-permission"):
            guard.validate_patched_sources(self.patched)

    def test_returning_to_pause_instead_of_playing_is_rejected(self):
        self.lose("crates/horizon-client/src/game/screens.rs", b"self.screen = Screen::Playing;", b"self.screen = Screen::Paused;")
        with self.assertRaisesRegex(ValueError, "pause-and-host-command-permission"):
            guard.validate_patched_sources(self.patched)

    def test_stale_chat_action_guard_cannot_be_removed(self):
        self.lose("crates/horizon-client/src/lanmc_steam.rs", b"current != Some(lobby)", b"false")
        with self.assertRaisesRegex(ValueError, "current-host-chat-actions"):
            guard.validate_patched_sources(self.patched)

    def test_window_and_taskbar_icon_hooks_are_both_required(self):
        for path, token in [
            ("crates/horizon-client/src/game/no_resources.rs", b"crate::lanmc_presentation::with_icon("),
            ("crates/horizon-client/src/game/split/windows.rs", b"crate::lanmc_presentation::with_icon("),
            ("crates/horizon-client/src/lanmc_presentation.rs", b"with_window_icon(icon.clone())"),
            ("crates/horizon-client/src/lanmc_presentation.rs", b"with_taskbar_icon(attributes, icon)"),
        ]:
            with self.subTest(path=path, token=token):
                broken = dict(self.patched)
                broken[path] = broken[path].replace(token, b"removed")
                with self.assertRaisesRegex(ValueError, "pack-window-title-and-icon"):
                    guard.validate_patched_sources(broken)

    def test_internal_port_notice_cannot_be_removed(self):
        self.lose("crates/horizon-client/src/game/lan.rs", "Локальный порт сервера: {port}".encode(), b"removed")
        with self.assertRaisesRegex(ValueError, "steam-publish-close-loopback"):
            guard.validate_patched_sources(self.patched)

    def test_public_server_seeding_cannot_return_to_normal_startup(self):
        for name in ("add_official_server", "add_partner_servers"):
            with self.subTest(name=name):
                broken = dict(self.patched)
                broken["crates/horizon-client/src/game.rs"] += (
                    "\nhorizon_client::multiplayer::" + name + "(&player_dir);"
                ).encode()
                with self.assertRaisesRegex(ValueError, "automatic public server seeding"):
                    guard.validate_patched_sources(broken)

    def test_commented_out_frontend_hook_does_not_count(self):
        self.lose("crates/horizon-client/src/game.rs", b"self.poll_steam_sharing();",
                  b"/* retired /* nested note */ self.poll_steam_sharing(); */")
        with self.assertRaisesRegex(ValueError, "steam-frontend-invitations"):
            guard.validate_patched_sources(self.patched)

    def test_callback_or_cancellation_regression_is_rejected(self):
        for path, token in [("crates/lanmc-steam-native/src/worker.rs", b"GameRichPresenceJoinRequested"),
                            ("crates/lanmc-steam-native/src/lib.rs", b"sharing_epoch.fetch_add")]:
            with self.subTest(token=token):
                broken = dict(self.patched)
                broken[path] = broken[path].replace(token, b"removed")
                with self.assertRaises(ValueError):
                    guard.validate_patched_sources(broken)

    def test_overlay_join_keeps_one_shot_and_fresh_friend_authorization(self):
        for path, token in [
            ("crates/lanmc-steam-native/src/invitation.rs", b"self.authorized.take()?"),
            ("crates/lanmc-steam-native/src/invitation.rs", b"now.checked_duration_since(since)? <= max_age"),
            ("crates/lanmc-steam-native/src/worker.rs", b"self.invited_world_owner(lobby, friend)"),
            ("crates/lanmc-steam-native/src/worker.rs", b"get_friend(inviter)"),
            ("crates/lanmc-steam-native/src/worker.rs", b"self.world_offer(lobby, *owner).is_some()"),
        ]:
            with self.subTest(path=path, token=token):
                broken = dict(self.patched)
                self.assertIn(token, broken[path])
                broken[path] = broken[path].replace(token, b"removed")
                with self.assertRaisesRegex(ValueError, "steam-frontend-invitations"):
                    guard.validate_patched_sources(broken)

    def test_releasing_ready_before_identity_claim_is_rejected(self):
        self.lose("crates/lanmc-steam-native/src/worker.rs",
                  b"shared.lock().unwrap().claims.insert(address, identity);", b"ready()?;")
        with self.assertRaisesRegex(ValueError, "sharing-cancellation-and-ready-claim"):
            guard.validate_patched_sources(self.patched)

    def test_missing_or_malformed_receipt_coverage_is_rejected(self):
        for value in [None, {}, {"patchedFiles": {}}, {"patchedFiles": []}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                guard.validate_receipt_coverage(value)
        for digest in [None, "", "f" * 63, "G" * 64, 13]:
            broken = copy.deepcopy(self.receipt)
            broken["patchedFiles"]["crates/horizon-client/src/game/chat.rs"] = digest
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                guard.validate_receipt_coverage(broken)

    def test_harmless_comments_and_crlf_do_not_break_source_contract(self):
        self.patched["crates/horizon-client/src/game.rs"] = self.patched["crates/horizon-client/src/game.rs"].replace(
            b"self.poll_steam_sharing();", b"/* local UI polling */ self.poll_steam_sharing();")
        self.patched = {path: data.replace(b"\n", b"\r\n") if path.endswith(".rs") else data
                        for path, data in self.patched.items()}
        guard.validate_patched_sources(self.patched)

    def test_removed_notice_module_or_final_ui_hook_is_rejected(self):
        for path in ["crates/horizon-client-render/src/gfx/lanmc_notice_state.rs",
                     "crates/horizon-client-render/src/gfx/lanmc_notice_protocol.rs",
                     "crates/horizon-client-render/src/gfx/lanmc_notice_raster.rs",
                     "crates/horizon-client-render/src/gfx/lanmc_notice_render.rs",
                     "crates/horizon-client-render/src/gfx/lanmc_notice_layout.rs",
                     "crates/lanmc-notice-platform/Cargo.toml",
                     "crates/lanmc-notice-platform/src/lib.rs"]:
            with self.subTest(path=path):
                broken = dict(self.patched)
                del broken[path]
                with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
                    guard.validate_patched_sources(broken)
        self.lose("crates/horizon-client-render/src/gfx.rs", b"self.lanmc_notices.draw(")
        with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
            guard.validate_patched_sources(self.patched)

    def test_notice_callback_cannot_move_before_present_on_any_presenter(self):
        for path, present, callback in [
            ("crates/horizon-client-render/src/gfx.rs", b"self.queue.present(surface_frame);",
             b"if let Some(callback) = after_present {\n                callback();\n            }"),
            ("crates/horizon-client-render/src/gfx/compositor.rs", b"self.queue.present(frame);",
             b"if let Some(callback) = after_present {\n            callback();\n        }"),
            ("crates/horizon-client-render/src/gfx/submit.rs", b"queue.present(surface);",
             b"if let Some(callback) = job.after_present {\n                            callback();\n                        }"),
        ]:
            with self.subTest(path=path):
                broken = dict(self.patched)
                self.assertIn(callback, broken[path])
                self.assertIn(present, broken[path])
                broken[path] = broken[path].replace(callback, b"").replace(present, callback + b"\n" + present)
                with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
                    guard.validate_patched_sources(broken)

    def test_commented_notice_draw_and_unguarded_generated_frame_ack_are_rejected(self):
        path = "crates/horizon-client-render/src/gfx.rs"
        data = self.patched[path]
        start = data.index(b"if self.surface.is_some()")
        end = data.index(b"        readback", start) + len(b"        readback")
        commented = dict(self.patched)
        commented[path] = data[:start] + b"/*" + data[start:end] + b"*/" + data[end:]
        with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
            guard.validate_patched_sources(commented)
        for path, old, new in [
            ("crates/horizon-client-render/src/gfx.rs", b"self.lanmc_notices.draw(", b"/* self.lanmc_notices.draw( */ removed("),
            ("crates/horizon-client-render/src/gfx/frame_gen.rs", b"if show(&pair.second)", b"if true"),
            ("crates/horizon-client-render/src/gfx.rs", b"if self.surface.is_some()", b"if true"),
        ]:
            with self.subTest(path=path):
                broken = dict(self.patched)
                self.assertIn(old, broken[path])
                broken[path] = broken[path].replace(old, new)
                with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
                    guard.validate_patched_sources(broken)

    def test_each_frame_generation_envelope_keeps_its_present_callback(self):
        path = "crates/horizon-client-render/src/gfx/frame_gen.rs"
        original = b"interval: self.interval,\n                after_present,"
        self.assertEqual(self.patched[path].count(original), 3)
        self.patched[path] = self.patched[path].replace(
            original, b"interval: self.interval,\n                removed,", 1)
        with self.assertRaisesRegex(ValueError, "lost a frame-generation presentation callback"):
            guard.validate_patched_sources(self.patched)

    def test_frame_generation_uses_the_callback_for_the_image_actually_shown(self):
        path = "crates/horizon-client-render/src/gfx.rs"
        for old, new in [
            (b"if frame_gen::generated_only() {\n                notice_generated_callback",
             b"if frame_gen::generated_only() {\n                after_present"),
            (b"else if frame_gen::generated_only() && self.surface.is_some()",
             b"else if false"),
        ]:
            with self.subTest(token=old):
                broken = dict(self.patched)
                self.assertIn(old, broken[path])
                broken[path] = broken[path].replace(old, new)
                with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
                    guard.validate_patched_sources(broken)

    def test_notice_protocol_or_dpi_contract_cannot_be_relaxed(self):
        notice = "crates/horizon-client-render/src/gfx/"
        for path, old in [
            (notice + "lanmc_notice_protocol.rs", b"title.encode_utf16().count() > 160"),
            (notice + "lanmc_notice_protocol.rs", b"now_ms.saturating_add(2_000)"),
            (notice + "lanmc_notice_state.rs", b"transformations=0"),
            (notice + "lanmc_notice_state.rs", b"std::env::var_os(\"LANMINECRAFT_NOTICE_DIRECTORY\")?"),
            ("crates/lanmc-notice-platform/src/lib.rs", b"s.width as f64 * dpi"),
            (notice + "lanmc_notice_layout.rs", b"width: card.width as f32,"),
        ]:
            with self.subTest(path=path, token=old):
                broken = dict(self.patched)
                self.assertIn(old, broken[path])
                broken[path] = broken[path].replace(old, b"removed")
                with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
                    guard.validate_patched_sources(broken)

    def test_windows_ffi_stays_in_the_local_platform_crate_behind_a_safe_adapter(self):
        adapter = "crates/horizon-client-render/src/gfx/lanmc_notice_raster.rs"
        unsafe = dict(self.patched)
        unsafe[adapter] += b"\npub unsafe fn bypass() {}\n"
        with self.assertRaisesRegex(ValueError, "requires a safe game adapter"):
            guard.validate_patched_sources(unsafe)
        for path, old, new in [
            ("crates/horizon-client-render/Cargo.toml",
             b'lanmc_notice_platform = { path = "../lanmc-notice-platform" }',
             b'lanmc_notice_platform = "0.1.0"'),
            ("crates/lanmc-notice-platform/src/lib.rs", b"#![forbid(unsafe_op_in_unsafe_fn)]", b""),
            (adapter, b"lanmc_notice_platform::rasterize(&converted, viewport_width)",
             b"bypass_platform(&converted, viewport_width)"),
        ]:
            with self.subTest(path=path):
                broken = dict(self.patched)
                self.assertIn(old, broken[path])
                broken[path] = broken[path].replace(old, new)
                with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
                    guard.validate_patched_sources(broken)

    def test_notice_modules_left_on_disk_cannot_be_orphaned_from_runtime(self):
        for filename, old, new in [
            ("lanmc_notices.rs", b"service: state::service(),", b"service: None,"),
            ("lanmc_notice_state.rs", b"protocol::parse_snapshot(style, notices, now)",
             b"unchecked_snapshot(style, notices, now)"),
            ("lanmc_notice_state.rs", b"raster::rasterize(&snapshot, width)", b"old_raster(&snapshot, width)"),
            ("lanmc_notice_render.rs", b"layout::layout(dimensions, &sizes, motion, now_ms)",
             b"scaled_twice(dimensions, &sizes, motion, now_ms)"),
        ]:
            path = "crates/horizon-client-render/src/gfx/" + filename
            with self.subTest(path=path):
                broken = dict(self.patched)
                self.assertIn(old, broken[path])
                broken[path] = broken[path].replace(old, new)
                with self.assertRaisesRegex(ValueError, "native-launcher-notices"):
                    guard.validate_patched_sources(broken)


if __name__ == "__main__":
    unittest.main()
