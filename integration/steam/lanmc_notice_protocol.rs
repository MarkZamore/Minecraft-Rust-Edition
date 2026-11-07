//! Bounded UTF-8 Java Properties contract owned by the launcher.
//!
//! This module has no game, UI or filesystem dependencies. A caller publishes
//! a new snapshot only when both complete documents pass this validation.
use std::collections::BTreeMap;

pub const MAX_BYTES: usize = 32 * 1024;
pub const LIFE_MS: u64 = 7_000;

#[derive(Clone, Debug, PartialEq)]
pub struct Style {
    pub bg_argb: u32,
    pub border_argb: u32,
    pub text_argb: u32,
    pub muted_argb: u32,
    pub font_family: String,
    pub font_size: f32,
    pub width: f32,
    pub gap: f32,
    pub padding: f32,
    pub radius: f32,
    pub rise_ms: u64,
    pub fade_ms: u64,
    pub life_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Notice {
    pub title: String,
    pub body: String,
    pub created_unix_ms: u64,
}

impl Notice {
    pub fn elapsed_ms(&self, now_ms: u64) -> u64 {
        now_ms.saturating_sub(self.created_unix_ms)
    }

    pub fn is_alive(&self, now_ms: u64) -> bool {
        self.created_unix_ms <= now_ms.saturating_add(2_000) && self.elapsed_ms(now_ms) < LIFE_MS
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Snapshot {
    pub style: Style,
    pub notices: Vec<Notice>,
    pub dpi_scale: f32,
}

pub fn parse_snapshot(
    style_bytes: &[u8],
    notice_bytes: &[u8],
    now_ms: u64,
) -> Result<Snapshot, String> {
    let style = properties(style_bytes)?;
    let notices = properties(notice_bytes)?;
    let font_family = required(&style, "fontFamily")?.to_owned();
    if font_family.trim().is_empty()
        || font_family.chars().count() > 128
        || font_family.chars().any(char::is_control)
    {
        return Err("Invalid fontFamily".into());
    }
    let life_ms = milliseconds(&style, "lifeMs")?;
    if life_ms != LIFE_MS {
        return Err("lifeMs must be 7000".into());
    }
    let style = Style {
        bg_argb: color(&style, "bgArgb")?,
        border_argb: color(&style, "borderArgb")?,
        text_argb: color(&style, "textArgb")?,
        muted_argb: color(&style, "mutedArgb")?,
        font_family,
        font_size: number(&style, "fontSize", 1.0, 128.0)?,
        width: number(&style, "width", 64.0, 4096.0)?,
        gap: number(&style, "gap", 0.0, 512.0)?,
        padding: number(&style, "padding", 0.0, 512.0)?,
        radius: number(&style, "radius", 0.0, 512.0)?,
        rise_ms: milliseconds(&style, "riseMs")?,
        fade_ms: milliseconds(&style, "fadeMs")?,
        life_ms,
    };
    // Prevent a malformed style from leaving no content area. The launcher
    // normally sends 320 DIP with a 16 DIP inset.
    if style.padding * 2.0 >= style.width {
        return Err("Notice padding consumes its width".into());
    }
    let dpi_scale = number(&notices, "dpiScale", 0.5, 4.0)?;
    let count = required(&notices, "count")?
        .trim()
        .parse::<usize>()
        .map_err(|_| "Invalid count".to_owned())?;
    if count > 3 {
        return Err("Notice count exceeds three".into());
    }
    let mut result = Vec::with_capacity(count);
    for index in 0..count {
        let prefix = format!("{index}.");
        let title = required(&notices, &(prefix.clone() + "title"))?.to_owned();
        let body = required(&notices, &(prefix.clone() + "body"))?.to_owned();
        if title.chars().count() > 160 || body.chars().count() > 320 {
            return Err("Notice text exceeds its character limit".into());
        }
        if title.contains('\0') || body.contains('\0') {
            return Err("Notice text contains NUL".into());
        }
        let created_unix_ms = required(&notices, &(prefix + "createdUnixMs"))?
            .trim()
            .parse::<u64>()
            .map_err(|_| "Invalid createdUnixMs".to_owned())?;
        if created_unix_ms > now_ms.saturating_add(2_000) {
            return Err("Notice timestamp is more than two seconds in the future".into());
        }
        result.push(Notice {
            title,
            body,
            created_unix_ms,
        });
    }
    Ok(Snapshot {
        style,
        notices: result,
        dpi_scale,
    })
}

fn required<'a>(values: &'a BTreeMap<String, String>, key: &str) -> Result<&'a str, String> {
    values
        .get(key)
        .map(String::as_str)
        .ok_or_else(|| format!("Missing {key}"))
}

fn color(values: &BTreeMap<String, String>, key: &str) -> Result<u32, String> {
    let raw = required(values, key)?.trim();
    let raw = raw.strip_prefix('#').unwrap_or(raw);
    if raw.len() != 8 || !raw.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(format!("Invalid {key}"));
    }
    u32::from_str_radix(raw, 16).map_err(|_| format!("Invalid {key}"))
}

fn number(
    values: &BTreeMap<String, String>,
    key: &str,
    low: f32,
    high: f32,
) -> Result<f32, String> {
    let value = required(values, key)?
        .trim()
        .parse::<f64>()
        .map_err(|_| format!("Invalid {key}"))?;
    if !value.is_finite() || !(f64::from(low)..=f64::from(high)).contains(&value) {
        return Err(format!("Out-of-range {key}"));
    }
    Ok(value as f32)
}

fn milliseconds(values: &BTreeMap<String, String>, key: &str) -> Result<u64, String> {
    let value = required(values, key)?
        .trim()
        .parse::<f64>()
        .map_err(|_| format!("Invalid {key}"))?;
    if !value.is_finite() || !(0.0..=LIFE_MS as f64).contains(&value) || value.fract() != 0.0 {
        return Err(format!("Non-integral {key}"));
    }
    Ok(value as u64)
}

fn space(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\u{000c}')
}

/// Java Properties Reader grammar: natural lines, continuations, escaped
/// separators, Unicode escapes and last-key-wins. UTF-8 is checked before any
/// replacement, so a truncated multibyte character cannot corrupt old state.
fn properties(bytes: &[u8]) -> Result<BTreeMap<String, String>, String> {
    if bytes.len() > MAX_BYTES {
        return Err("Properties document exceeds 32 KiB".into());
    }
    let text = std::str::from_utf8(bytes).map_err(|_| "Properties are not UTF-8".to_owned())?;
    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut logical = String::new();
    let mut continuing = false;
    let mut result = BTreeMap::new();
    for natural in normalized.split('\n') {
        let line = natural.trim_start_matches(space);
        if !continuing && (line.is_empty() || line.starts_with(['#', '!'])) {
            continue;
        }
        let slashes = line.bytes().rev().take_while(|byte| *byte == b'\\').count();
        let continued = slashes % 2 == 1;
        logical.push_str(if continued {
            &line[..line.len() - 1]
        } else {
            line
        });
        if continued {
            continuing = true;
            continue;
        }
        insert_property(&mut result, &logical)?;
        logical.clear();
        continuing = false;
    }
    // Java's line reader drops a continuation slash at EOF.
    if continuing {
        insert_property(&mut result, &logical)?;
    }
    Ok(result)
}

fn insert_property(values: &mut BTreeMap<String, String>, line: &str) -> Result<(), String> {
    let mut escaped = false;
    let mut split = None;
    for (index, c) in line.char_indices() {
        if !escaped && (space(c) || matches!(c, '=' | ':')) {
            split = Some((index, c.len_utf8(), space(c)));
            break;
        }
        if c == '\\' {
            escaped = !escaped;
        } else {
            escaped = false;
        }
    }
    let (raw_key, raw_value) = if let Some((index, length, whitespace)) = split {
        let mut rest = &line[index + length..];
        if whitespace {
            rest = rest.trim_start_matches(space);
            if rest.starts_with(['=', ':']) {
                rest = &rest[1..];
            }
        }
        (&line[..index], rest.trim_start_matches(space))
    } else {
        (line, "")
    };
    values.insert(unescape(raw_key)?, unescape(raw_value)?);
    Ok(())
}

fn unescape(value: &str) -> Result<String, String> {
    let mut chars = value.chars();
    let mut units = Vec::<u16>::with_capacity(value.len());
    while let Some(c) = chars.next() {
        if c != '\\' {
            let mut encoded = [0; 2];
            units.extend_from_slice(c.encode_utf16(&mut encoded));
            continue;
        }
        let Some(next) = chars.next() else {
            // Natural-line processing removes an odd final backslash.
            return Err("Truncated Properties escape".into());
        };
        let decoded = match next {
            'u' => {
                let mut unit = 0_u16;
                for _ in 0..4 {
                    let digit = chars
                        .next()
                        .and_then(|c| c.to_digit(16))
                        .ok_or_else(|| "Malformed Properties Unicode escape".to_owned())?;
                    unit = unit * 16 + digit as u16;
                }
                units.push(unit);
                continue;
            }
            't' => '\t',
            'n' => '\n',
            'r' => '\r',
            'f' => '\u{000c}',
            other => other,
        };
        let mut encoded = [0; 2];
        units.extend_from_slice(decoded.encode_utf16(&mut encoded));
    }
    String::from_utf16(&units).map_err(|_| "Unpaired Properties Unicode surrogate".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    const STYLE: &str = "bgArgb=FF112233\nborderArgb=FF445566\ntextArgb=FFFFFFFF\nmutedArgb=FFAABBCC\nfontFamily=Segoe UI Variable Text\nfontSize=14\nwidth=320\ngap=24\npadding=16\nradius=8\nriseMs=200\nfadeMs=350\nlifeMs=7000\n";
    const NOTICES: &str =
        "count=1\ndpiScale=1.5\n0.title=Друзья\n0.body=Мир открыт\n0.createdUnixMs=10000\n";

    fn parse(style: &str, notices: &str, now: u64) -> Result<Snapshot, String> {
        parse_snapshot(style.as_bytes(), notices.as_bytes(), now)
    }

    #[test]
    fn launcher_snapshot_preserves_style_dpi_and_cyrillic() {
        let snapshot = parse(STYLE, NOTICES, 10_100).unwrap();
        assert_eq!(snapshot.style.bg_argb, 0xff11_2233);
        assert_eq!(snapshot.style.font_family, "Segoe UI Variable Text");
        assert_eq!(snapshot.style.life_ms, 7000);
        assert_eq!(snapshot.dpi_scale, 1.5);
        assert_eq!(snapshot.notices[0].title, "Друзья");
        assert_eq!(snapshot.notices[0].body, "Мир открыт");
    }

    #[test]
    fn java_reader_escapes_comments_separators_and_continuations() {
        let text = " # comment\\\r\n! another\rkey\\ with\\:colon : first\\\r\n  second\\nline\\t\\u041C\\u0438\\u0440\\q\rkey\\ with\\:colon=last\nempty\n=empty-key\n";
        let values = properties(text.as_bytes()).unwrap();
        assert_eq!(values["key with:colon"], "last");
        assert_eq!(values["empty"], "");
        assert_eq!(values[""], "empty-key");
        let values = properties(b"v=first\\\n\tsecond\\nline\\t\\u041C\\u0438\\u0440\\q").unwrap();
        assert_eq!(values["v"], "firstsecond\nline\tМирq");
    }

    #[test]
    fn escaped_leading_space_and_even_backslash_do_not_continue() {
        let values = properties(b"key=\\ leading\\ \\n\nslash=tail\\\\\nnext=x\n").unwrap();
        assert_eq!(values["key"], " leading \n");
        assert_eq!(values["slash"], "tail\\");
        assert_eq!(values["next"], "x");
    }

    #[test]
    fn utf16_surrogate_pair_decodes_without_loss() {
        let values = properties(b"emoji=\\uD83D\\uDE00\n").unwrap();
        assert_eq!(values["emoji"], "😀");
        for invalid in [
            b"v=\\uD83D".as_slice(),
            b"v=\\uDE00",
            b"v=\\u12G4",
            b"v=\\u123",
        ] {
            assert!(properties(invalid).is_err());
        }
    }

    #[test]
    fn invalid_utf8_or_oversized_document_never_becomes_a_snapshot() {
        assert!(parse_snapshot(STYLE.as_bytes(), b"count=0\ndpiScale=1\n\xff", 1).is_err());
        assert!(parse_snapshot(&vec![b'#'; MAX_BYTES + 1], NOTICES.as_bytes(), 10_000).is_err());
        assert!(parse_snapshot(STYLE.as_bytes(), &vec![b'#'; MAX_BYTES + 1], 10_000).is_err());
    }

    #[test]
    fn missing_or_truncated_required_fields_are_not_defaulted() {
        for missing in ["fontFamily", "fontSize", "bgArgb", "lifeMs"] {
            let style = STYLE
                .lines()
                .filter(|line| !line.starts_with(missing))
                .collect::<Vec<_>>()
                .join("\n");
            assert!(parse(&style, NOTICES, 10_000).is_err(), "{missing}");
        }
        for notices in ["count=0", "dpiScale=1", "count=1\ndpiScale=1\n0.title=x\n"] {
            assert!(parse(STYLE, notices, 10_000).is_err());
        }
    }

    #[test]
    fn nonfinite_or_unsafe_geometry_and_wrong_lifetime_are_rejected() {
        for (old, new) in [
            ("fontSize=14", "fontSize=NaN"),
            ("width=320", "width=inf"),
            ("width=320", "width=-1"),
            ("padding=16", "padding=200"),
            ("riseMs=200", "riseMs=200.25"),
            ("riseMs=200", "riseMs=200.000001"),
            ("fadeMs=350", "fadeMs=7001"),
            ("lifeMs=7000", "lifeMs=7001"),
        ] {
            assert!(
                parse(&STYLE.replace(old, new), NOTICES, 10_000).is_err(),
                "{new}"
            );
        }
        let floating = STYLE.replace("riseMs=200", "riseMs=200.0");
        assert_eq!(
            parse(&floating, NOTICES, 10_000).unwrap().style.rise_ms,
            200
        );
    }

    #[test]
    fn count_dpi_text_and_nul_limits_are_enforced() {
        for invalid in [
            NOTICES.replace("count=1", "count=4"),
            NOTICES.replace("count=1", "count=-1"),
            NOTICES.replace("dpiScale=1.5", "dpiScale=NaN"),
            NOTICES.replace("dpiScale=1.5", "dpiScale=4.01"),
            NOTICES.replace("dpiScale=1.5", "dpiScale=4.00000001"),
            NOTICES.replace("dpiScale=1.5", "dpiScale=0.49"),
            NOTICES.replace(
                "0.title=Друзья",
                &("0.title=".to_owned() + &"ж".repeat(161)),
            ),
            NOTICES.replace(
                "0.body=Мир открыт",
                &("0.body=".to_owned() + &"a".repeat(321)),
            ),
            NOTICES.replace("0.body=Мир открыт", "0.body=\\u0000"),
        ] {
            assert!(parse(STYLE, &invalid, 10_000).is_err());
        }
    }

    #[test]
    fn timestamp_bounds_and_expiry_do_not_overflow_or_revive() {
        let notice = parse(STYLE, NOTICES, 8_000).unwrap().notices.remove(0);
        assert!(notice.is_alive(8_000));
        assert_eq!(notice.elapsed_ms(8_000), 0);
        assert!(parse(STYLE, NOTICES, 7_999).is_err());
        assert!(notice.is_alive(16_999));
        assert!(!notice.is_alive(17_000));
        assert!(!notice.is_alive(u64::MAX));
        assert!(
            parse(
                STYLE,
                &NOTICES.replace("10000", "18446744073709551616"),
                10_000
            )
            .is_err()
        );
    }

    #[test]
    fn empty_queue_is_a_valid_clear_snapshot() {
        let snapshot = parse(STYLE, "count=0\ndpiScale=0.5\n", 10_000).unwrap();
        assert!(snapshot.notices.is_empty());
        assert_eq!(snapshot.dpi_scale, 0.5);
    }
}
