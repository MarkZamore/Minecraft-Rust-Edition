"""Share Horizon's normal title keyboard handling with its opted-in Agent Control."""

from hashlib import sha256
from textwrap import dedent, indent


BASE = "crates/horizon-client/src/"
GAME = BASE + "game.rs"
CLIPBOARD = BASE + "game/interface/clipboard.rs"
CONTROL = BASE + "game/runtime/control.rs"
SOURCES = (CLIPBOARD, CONTROL)

TITLE_ARM = "                WindowEvent::KeyboardInput { event, .. } if self.title_input_active() => {\n"
NEXT_ARM = "                WindowEvent::KeyboardInput { event, .. } => {\n"
ARM_SHA = "961ef1ea06c64cb654d5a6a1acff55fff8ec0dc44d961cbb04dfc7975970858b"


TITLE_PRESS = """    fn title_key_pressed(
        &mut self,
        event_loop: &dyn EventLoopCtl,
        physical: PhysicalKey,
        shortcut: Option<horizon_client::clipboard::EditShortcut>,
        text: Option<&str>,
    ) -> Result<()> {
{body}
        Ok(())
    }

    fn server_title_control_active(&self) -> bool {
        self.window.is_some()
            && self.title_input_active()
            && self.screen != Screen::Options
            && self.command_prompt.is_none()
            && !self.dialog_open()
            && !self.remote_loading()
            && self.title.as_ref().is_some_and(|title| {
                matches!(title.mode, horizon_client::title::Mode::Multiplayer(_))
            })
    }

"""

SOCKET_KEY = """            "title-key" => {
                if !self.server_title_control_active() {
                    return Err("title-key requires the active multiplayer title screen".into());
                }
                let name = arg(1)?;
                let request = arg(2)?;
                if words.len() != 3
                    || !(1..=64).contains(&request.len())
                    || !request.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
                {
                    return Err("usage: title-key KEY REQUEST".into());
                }
                let key = match name {
                    "enter" => KeyCode::Enter,
                    "delete" => KeyCode::Delete,
                    "f5" => KeyCode::F5,
                    "escape" => KeyCode::Escape,
                    "backspace" => KeyCode::Backspace,
                    "ctrl-a" => KeyCode::KeyA,
                    _ => return Err("unsupported multiplayer title key".into()),
                };
                let shortcut = if name == "ctrl-a" {
                    // Resolve Select All through the same recognizer as a
                    // window key without changing desktop or game focus.
                    let modifier = if cfg!(target_os = "macos") {
                        KeyCode::SuperLeft
                    } else {
                        KeyCode::ControlLeft
                    };
                    let before = self.modifiers;
                    let held = self.physical_keys.contains(&modifier);
                    self.press_modifier_key(modifier, true);
                    let shortcut = self.edit_shortcut_letter(Some('a'));
                    self.press_modifier_key(modifier, held);
                    self.modifiers = before;
                    if !matches!(shortcut, Some(horizon_client::clipboard::EditShortcut::SelectAll)) {
                        return Err("the active shortcut modifiers do not select all".into());
                    }
                    shortcut
                } else {
                    None
                };
                self.title_key_pressed(event_loop, winit::keyboard::PhysicalKey::Code(key), shortcut, None)?;
                Ok(Some(json!({
                    "route": "horizon-agent-control/title-key-v1",
                    "processId": std::process::id(),
                    "requestId": request,
                    "key": name,
                })))
            }
"""


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected one reviewed multiplayer title input anchor: {old[:100]!r}")
    return text.replace(old, new, 1)


def apply(patched):
    game = patched[GAME].decode("utf-8")
    start = game.index(TITLE_ARM)
    end = game.index(NEXT_ARM, start + len(TITLE_ARM))
    original = game[start:end]
    if sha256(original.encode("utf-8")).hexdigest() != ARM_SHA:
        raise ValueError("Horizon title window key dispatch changed")
    press = "                    if event.state == ElementState::Pressed {\n"
    close = "                    }\n                }\n"
    body = dedent(original.removeprefix(TITLE_ARM + press).removesuffix(close))
    body = replace(body, "self.edit_shortcut(&event)", "shortcut")
    body = replace(body, "event.text.as_deref()", "text")
    if body.count("event.physical_key") != 2:
        raise ValueError("Unexpected Horizon title key identity")
    body = body.replace("event.physical_key", "physical")
    arm = (TITLE_ARM + press
           + "                        let shortcut = self.edit_shortcut(&event);\n"
           + "                        self.title_key_pressed(event_loop, event.physical_key, shortcut, event.text.as_deref())?;\n"
           + close)
    game = game[:start] + arm + game[end:]
    anchor = "    fn handle_window_event_inner(&mut self, event_loop: &dyn EventLoopCtl, mut event: WindowEvent) {\n"
    patched[GAME] = replace(game, anchor, TITLE_PRESS.replace("{body}", indent(body.rstrip(), " " * 8)) + anchor).encode("utf-8")

    clipboard = patched[CLIPBOARD].decode("utf-8")
    anchor = "        let m = self.modifiers;\n"
    updated = replace(clipboard, anchor, """
        self.edit_shortcut_letter(clipboard::shortcut_letter(&event.logical_key, event.physical_key))
    }

    pub(super) fn edit_shortcut_letter(&self, letter: Option<char>) -> Option<EditShortcut> {
        let m = self.modifiers;
""".lstrip("\n"))
    # The value in the new delegate and the existing recognizer are the two
    # occurrences. Only the recognizer should read the normalized letter.
    patched[CLIPBOARD] = replace(
        updated, "            clipboard::shortcut_letter(&event.logical_key, event.physical_key),\n",
        "            letter,\n",
    ).encode("utf-8")

    control = patched[CONTROL].decode("utf-8")
    control = replace(control, 'Some("key" | "mouse" | "type")', 'Some("key" | "mouse" | "type" | "title-key")')
    control = replace(control, '            "key" => {\n', SOCKET_KEY + '            "key" => {\n')
    control = replace(control,
        "                self.require_world()?;\n                // Typed keys:",
        "                if !(self.server_title_control_active() && self.text_field_focused()) {\n"
        "                    self.require_world()?;\n"
        "                }\n"
        "                // Typed keys:",
    )
    patched[CONTROL] = control.encode("utf-8")
