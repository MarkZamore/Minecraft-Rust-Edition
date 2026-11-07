"""Compile the exact producer's pure Rust selection helpers against discovery snapshots.

This exercises shipped helper bodies, not a second Python implementation. The
minimal types replace only unrelated rendering and Steam SDK dependencies; the
actual Windows UI/transport acceptance is a separate required check.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("selection_source_overlay", ROOT / "integration/steam/apply_horizon_overlay.py")
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)


def function(source, name):
    start = source.index(f"fn {name}(")
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        end += 1
    return source[start:end]


STUBS = r'''
#![allow(dead_code)]
mod lan { pub mod discovery {
    #[derive(Clone)] pub struct LanGame { pub address: String }
} }
use lan::discovery::LanGame;
use std::collections::HashMap;
struct ServerEntry { ip: String }
impl ServerEntry { fn new(_name: &str, ip: &str) -> Self { Self { ip: ip.to_owned() } } }
struct Scanner { games: Vec<LanGame> }
impl Scanner { fn games(&self) -> Vec<LanGame> { self.games.clone() } }
#[derive(Clone)] struct Text(String);
impl Text {
    fn empty() -> Self { Self(String::new()) }
    fn literal(s: impl Into<String>) -> Self { Self(s.into()) }
    fn plain(&self) -> String { self.0.clone() }
    fn with_color(self, _color: u32) -> Self { self }
}
mod text { pub const RED: u32 = 0; }
mod lang { pub fn get_or(_key: &str, fallback: &str) -> String { fallback.to_owned() } }
fn format_translation(format: &str, values: &[Text]) -> Text {
    Text(values.iter().fold(format.to_owned(), |s, value| s.replacen("%s", &value.0, 1)))
}
enum Ping {
    Pinging,
    Ok { motd: Text, players: Option<(i64, i64)>, player_list: Vec<Text>, ms: u128, version: String },
    Incompatible { motd: Text, players: Option<(i64, i64)>, player_list: Vec<Text>, version: Text },
    Failed(String),
}
impl Ping { fn player_count(_players: Option<(i64, i64)>) -> Text { Text::empty() } }
'''

TESTS = r'''
fn games(addresses: &[&str]) -> Vec<LanGame> {
    addresses.iter().map(|address| LanGame { address: (*address).to_owned() }).collect()
}
#[test]
fn reordered_world_keeps_the_selected_address_instead_of_the_old_row() {
    let scanner = Scanner { games: games(&["222.steam", "111.steam"]) };
    assert_eq!(selected_row_index(&[], &scanner.games(), Some(0), Some("111.steam")), Some(1));
    assert_eq!(selected_entry_address(&[], &scanner, Some(0), Some("111.steam")), Some(("111.steam".into(), true)));
}
#[test]
fn removed_world_never_connects_the_other_world_now_at_its_old_index() {
    let scanner = Scanner { games: games(&["222.steam"]) };
    assert_eq!(selected_row_index(&[], &scanner.games(), Some(0), Some("111.steam")), None);
    assert_eq!(selected_entry_address(&[], &scanner, Some(0), Some("111.steam")), None);
}
#[test]
fn unbound_numeric_selection_cannot_join_an_automatic_world_or_hidden_saved_row() {
    let scanner = Scanner { games: games(&["222.steam"]) };
    assert_eq!(selected_row_index(&[], &scanner.games(), Some(0), None), None);
    assert_eq!(selected_entry_address(&[], &scanner, Some(0), None), None);
    let saved = [ServerEntry { ip: "manual.example.invalid:25565".into() }];
    assert_eq!(selected_entry_address(&saved, &scanner, Some(0), Some("111.steam")), None);
}
#[test]
fn an_explicit_saved_selection_joins_only_its_actual_saved_entry() {
    let scanner = Scanner { games: Vec::new() };
    let saved = [ServerEntry { ip: "manual.example.invalid:25565".into() }];
    assert_eq!(selected_row_index(&saved, &[], Some(0), None), Some(0));
    assert_eq!(selected_entry_address(&saved, &scanner, Some(0), None), Some(("manual.example.invalid:25565".into(), false)));
    assert_eq!(selected_entry_address(&saved, &scanner, Some(1), None), None);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", required=True, type=Path)
    parser.add_argument("--steam-transport", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    patched = overlay.changes(overlay.locked_inputs(args.source_archive), args.steam_transport.resolve(strict=True))
    source = patched["crates/horizon-client/src/multiplayer.rs"]
    decoded = source.decode("utf-8")
    constants = "\n".join(re.search(rf"const {name}: f32 = [^;]+;", decoded)[0]
                          for name in ["VIEW_Y", "HEADER", "FOOTER", "ROW_WIDTH", "ROW_HEIGHT"])
    row_start = decoded.index("enum Row {")
    row = "#[derive(Clone, Copy, Debug, PartialEq)]\n" + decoded[row_start:decoded.index("}", row_start) + 1]
    helpers = "\n".join(function(decoded, name) for name in [
        "selected_row_index", "selected_entry_address", "list_area", "rows", "row_at",
        "content_height", "ping_tooltip", "player_list_tooltip"])
    author_tests = "\n".join("#[test]\n" + function(decoded, name) for name in [
        "ping_tooltip_follows_only_visible_status_bars_and_current_ping",
        "player_list_tooltip_covers_only_the_player_count",
        "rows_sit_two_pixels_under_the_header_and_scroll_with_the_list"])
    out = args.out.resolve()
    if out.exists():
        raise ValueError("Use a fresh unit-QA directory in a registered task stand")
    out.mkdir(parents=True)
    rust = out / "selection-tests.rs"
    executable = out / "selection-tests.exe"
    rust.write_text(STUBS + constants + row + helpers + TESTS + author_tests, encoding="utf-8")
    result = None
    try:
        subprocess.run(["rustc", "--edition=2024", "--test", str(rust), "-o", str(executable)],
                       check=True, capture_output=True, text=True)
        result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
        if not re.search(r"test result: ok\. 7 passed; 0 failed;", result.stdout):
            raise RuntimeError("Rust helper/author geometry tests did not report all seven executed cases")
        report = {"status": "PASS", "passed": 7, "failed": 0,
                  "scope": "exact Rust helpers and three author geometry tests; stubbed rendering/SDK types, not live Steam",
                  "upstreamCommit": overlay.COMMIT,
                  "multiplayerSourceSha256": hashlib.sha256(source).hexdigest(),
                  "helperSourceSha256": hashlib.sha256(helpers.encode()).hexdigest(),
                  "output": result.stdout}
        (out / "selection-unit-qa.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: report[key] for key in ["status", "passed", "failed", "helperSourceSha256"]}))
    finally:
        for path in [rust, executable, executable.with_suffix(".pdb")]:
            if path.exists() and path.is_file() and not path.is_symlink():
                path.unlink()


if __name__ == "__main__":
    main()
