//! Declarative defaults for the game child only. Parent process state is read,
//! never changed; the inheriting game command retains every explicit override.

use anyhow::Result;
use serde::{Deserialize, Deserializer, de};
use serde_json::{Map, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    ffi::{OsStr, OsString},
    fmt,
    io::Read,
    process::Command,
};

const MAX_KEYS: usize = 16;
const MAX_NAME_BYTES: usize = 64;
const MAX_VALUE_BYTES: usize = 1024;
const MAX_TOTAL_BYTES: usize = 8192;

#[derive(Default)]
pub(crate) struct EnvironmentDefaults(BTreeMap<String, String>);

impl<'de> Deserialize<'de> for EnvironmentDefaults {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct DefaultsVisitor;
        impl<'de> de::Visitor<'de> for DefaultsVisitor {
            type Value = EnvironmentDefaults;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("an environmentDefaults object of bounded string values")
            }

            fn visit_map<A: de::MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut values = BTreeMap::new();
                let mut names = BTreeSet::new();
                let mut total = 0;
                while let Some(name) = map.next_key::<String>()? {
                    if names.len() == MAX_KEYS {
                        return Err(de::Error::custom("environmentDefaults exceeds 16 entries"));
                    }
                    let mut bytes = name.bytes();
                    let first = bytes.next();
                    if name.len() > MAX_NAME_BYTES
                        || !first.is_some_and(|c| c.is_ascii_alphabetic() || c == b'_')
                        || !bytes.all(|c| c.is_ascii_alphanumeric() || c == b'_')
                    {
                        return Err(de::Error::custom(
                            "Invalid environmentDefaults variable name",
                        ));
                    }
                    // Check while deserializing: an ordinary JSON map would
                    // silently discard identical duplicate keys before validation.
                    if !names.insert(name.to_ascii_uppercase()) {
                        return Err(de::Error::custom(
                            "Duplicate environmentDefaults variable name",
                        ));
                    }
                    let value = map.next_value::<String>()?;
                    if value.len() > MAX_VALUE_BYTES || value.chars().any(char::is_control) {
                        return Err(de::Error::custom(
                            "Invalid environmentDefaults variable value",
                        ));
                    }
                    total += name.len() + value.len();
                    if total > MAX_TOTAL_BYTES {
                        return Err(de::Error::custom("environmentDefaults exceeds 8192 bytes"));
                    }
                    values.insert(name, value);
                }
                Ok(EnvironmentDefaults(values))
            }
        }
        deserializer.deserialize_map(DefaultsVisitor)
    }
}

/// Preserve the existing extensible presentation fields while reading defaults
/// directly through MapAccess, before JSON can discard duplicate default keys.
pub(crate) fn read_presentation(reader: impl Read) -> Result<(Value, EnvironmentDefaults)> {
    #[derive(Deserialize)]
    struct Document {
        #[serde(rename = "environmentDefaults", default)]
        defaults: EnvironmentDefaults,
        #[serde(flatten)]
        fields: Map<String, Value>,
    }
    let document: Document = serde_json::from_reader(reader)?;
    Ok((Value::Object(document.fields), document.defaults))
}

impl EnvironmentDefaults {
    /// The production game command inherits its environment. Read-only var_os
    /// preserves present-but-empty and non-Unicode inherited values as overrides.
    pub(crate) fn apply(&self, command: &mut Command) {
        self.apply_with_parent(command, |name| env::var_os(name));
    }

    fn apply_with_parent(
        &self,
        command: &mut Command,
        mut inherited: impl FnMut(&OsStr) -> Option<OsString>,
    ) {
        for (name, value) in &self.0 {
            if inherited(OsStr::new(name)).is_none() && !has_command_override(command, name) {
                command.env(name, value);
            }
        }
    }
}

fn has_command_override(command: &Command, name: &str) -> bool {
    // A never-executed Command uses the standard library's exact platform key
    // comparison, including Windows Unicode aliases. No Windows FFI is needed.
    let mut probe = Command::new(command.get_program());
    probe.env(name, "");
    for (key, _) in command.get_envs() {
        probe.env_remove(key);
    }
    // The sole Some entry disappears if any explicit setting/removal aliases it.
    !probe.get_envs().any(|(_, value)| value.is_some())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const TEST_KEY: &str = "LANMC_ENV_DEFAULTS_UNIT_CHILD_VALUE";
    const TEST_MODE: &str = "LANMC_ENV_DEFAULTS_UNIT_CHILD_MODE";
    const PROBE_PREFIX: &str = "ENV_DEFAULTS_PROBE=";

    fn parse(text: &str) -> Result<EnvironmentDefaults> {
        read_presentation(text.as_bytes()).map(|(_, defaults)| defaults)
    }

    fn defaults(value: Value) -> Result<EnvironmentDefaults> {
        parse(&json!({"environmentDefaults": value}).to_string())
    }

    fn entries(command: &Command) -> BTreeMap<OsString, Option<OsString>> {
        command
            .get_envs()
            .map(|(key, value)| (key.to_owned(), value.map(OsStr::to_owned)))
            .collect()
    }

    #[test]
    fn absent_and_empty_defaults_preserve_existing_presentation() -> Result<()> {
        let original =
            json!({"name": "Test", "instanceConfig": {"TEST_CONFIG": "test.json"}, "future": 7});
        let (presentation, absent) = read_presentation(original.to_string().as_bytes())?;
        assert_eq!(presentation, original);
        assert!(absent.0.is_empty());
        assert!(defaults(json!({}))?.0.is_empty());
        Ok(())
    }

    #[test]
    fn defaults_reject_null_non_objects_and_non_string_values() {
        for value in [
            Value::Null,
            json!([]),
            json!(false),
            json!(7),
            json!("text"),
            json!({"A": null}),
            json!({"A": 1}),
            json!({"A": false}),
            json!({"A": []}),
        ] {
            assert!(defaults(value.clone()).is_err(), "{value}");
        }
    }

    #[test]
    fn raw_duplicate_names_and_windows_case_aliases_are_rejected() {
        for text in [
            r#"{"environmentDefaults":{"FOO":"a","FOO":"b"}}"#,
            r#"{"environmentDefaults":{"FOO":"a","foo":"b"}}"#,
            r#"{"environmentDefaults":{},"environmentDefaults":{}}"#,
        ] {
            assert!(parse(text).is_err(), "{text}");
        }
    }

    #[test]
    fn key_count_is_bounded() -> Result<()> {
        let mut map: Map<String, Value> =
            (0..16).map(|i| (format!("KEY_{i}"), json!(""))).collect();
        assert_eq!(defaults(Value::Object(map.clone()))?.0.len(), 16);
        map.insert("KEY_16".into(), json!(""));
        assert!(defaults(Value::Object(map)).is_err());
        Ok(())
    }

    #[test]
    fn names_have_ascii_syntax_and_byte_bounds() -> Result<()> {
        defaults(json!({"a_B09": "", "_": ""}))?;
        defaults(json!({("A".repeat(64)): ""}))?;
        for key in [
            "".into(),
            "A".repeat(65),
            "9START".into(),
            "A-B".into(),
            "A=B".into(),
            "Å".into(),
            "A B".into(),
        ] {
            assert!(defaults(json!({(key): ""})).is_err());
        }
        Ok(())
    }

    #[test]
    fn values_are_bounded_by_utf8_bytes() -> Result<()> {
        defaults(json!({"A": "x".repeat(1024)}))?;
        defaults(json!({"A": "é".repeat(512)}))?;
        assert!(defaults(json!({"A": "x".repeat(1025)})).is_err());
        assert!(defaults(json!({"A": "é".repeat(513)})).is_err());
        Ok(())
    }

    #[test]
    fn values_reject_control_characters_but_allow_unicode_and_empty() -> Result<()> {
        defaults(json!({"A": "", "B": "Текст 😀"}))?;
        for value in [
            "nul\0",
            "line\n",
            "line\r",
            "tab\t",
            "del\u{7f}",
            "c1\u{85}",
        ] {
            assert!(defaults(json!({"A": value})).is_err());
        }
        Ok(())
    }

    #[test]
    fn aggregate_limit_counts_names_and_utf8_values() -> Result<()> {
        let mut map: Map<String, Value> = (0..8)
            .map(|i| (format!("K{i:02}"), json!("x".repeat(1021))))
            .collect();
        defaults(Value::Object(map.clone()))?; // 8 * (3 + 1021) = 8192.
        map.insert("K00".into(), json!("x".repeat(1022)));
        assert!(defaults(Value::Object(map)).is_err());
        Ok(())
    }

    #[test]
    fn inherited_nonempty_and_empty_values_are_not_overridden() -> Result<()> {
        let defaults = defaults(json!({"TEST": "default"}))?;
        for inherited in [OsString::from("explicit"), OsString::new()] {
            let mut command = Command::new("client");
            defaults.apply_with_parent(&mut command, |_| Some(inherited.clone()));
            assert!(entries(&command).is_empty());
        }
        Ok(())
    }

    #[test]
    fn inherited_non_unicode_value_is_not_overridden() -> Result<()> {
        use std::os::windows::ffi::OsStringExt;
        let defaults = defaults(json!({"TEST": "default"}))?;
        let mut command = Command::new("client");
        defaults.apply_with_parent(&mut command, |_| Some(OsString::from_wide(&[0xd800])));
        assert!(entries(&command).is_empty());
        Ok(())
    }

    #[test]
    fn explicit_command_values_and_removals_win_including_aliases() -> Result<()> {
        let defaults = defaults(json!({"TEST": "default"}))?;
        for value in [Some("explicit"), Some(""), None] {
            let mut command = Command::new("client");
            match value {
                Some(value) => {
                    command.env("test", value);
                }
                None => {
                    command.env_remove("test");
                }
            }
            command.env("UNRELATED", "keep");
            let before = entries(&command);
            defaults.apply_with_parent(&mut command, |_| None);
            assert_eq!(entries(&command), before);
        }
        Ok(())
    }

    #[test]
    fn unicode_command_names_follow_platform_alias_rules() -> Result<()> {
        for (name, other) in [("I", "ı"), ("K", "K"), ("S", "ſ")] {
            // Unicode case mappings differ from linguistic/Turkish folding.
            // Establish aliases using real Command insertions on this Windows.
            let mut native_comparison = Command::new("client");
            native_comparison.env(name, "first").env(other, "second");
            let aliases = native_comparison.get_envs().count() == 1;
            let defaults = defaults(json!({(name): "default"}))?;
            for value in [Some("explicit"), None] {
                let mut command = Command::new("client");
                match value {
                    Some(value) => {
                        command.env(other, value);
                    }
                    None => {
                        command.env_remove(other);
                    }
                }
                assert_eq!(has_command_override(&command, name), aliases);
                let before = entries(&command);
                defaults.apply_with_parent(&mut command, |_| None);
                let after = entries(&command);
                assert!(
                    before
                        .iter()
                        .all(|(key, value)| after.get(key) == Some(value))
                );
                if aliases {
                    assert_eq!(after, before);
                } else {
                    assert_eq!(after.get(OsStr::new(name)), Some(&Some("default".into())));
                }
            }
        }
        Ok(())
    }

    fn child_command(mode: &str) -> Result<Command> {
        let mut command = Command::new(env::current_exe()?);
        command.args([
            "--exact",
            "environment_defaults::tests::child_environment_probe",
            "--nocapture",
        ]);
        command.env(TEST_MODE, mode);
        Ok(command)
    }

    fn probe_output(command: &mut Command) -> Result<Option<String>> {
        let output = command.output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let text = String::from_utf8(output.stdout)?;
        let encoded = text
            .lines()
            .find_map(|line| line.split_once(PROBE_PREFIX).map(|(_, value)| value))
            .expect("Actual child did not report its environment");
        Ok(serde_json::from_str(encoded)?)
    }

    #[test]
    fn actual_child_gets_defaults_without_changing_parent_or_other_child() -> Result<()> {
        let defaults = defaults(json!({(TEST_KEY): "Текст 😀"}))?;
        let parent_before = env::var_os(TEST_KEY);
        let mut client = child_command("probe")?;
        client.env_clear().env(TEST_MODE, "probe");
        defaults.apply_with_parent(&mut client, |_| None);
        assert_eq!(probe_output(&mut client)?, Some("Текст 😀".into()));
        let mut unrelated = child_command("probe")?;
        unrelated.env_clear().env(TEST_MODE, "probe");
        assert_eq!(probe_output(&mut unrelated)?, None);
        assert_eq!(env::var_os(TEST_KEY), parent_before);
        Ok(())
    }

    #[test]
    fn actual_inheriting_child_keeps_parent_override_including_empty() -> Result<()> {
        for value in ["explicit", ""] {
            let mut parent = child_command("inherited")?;
            parent
                .env_clear()
                .env(TEST_MODE, "inherited")
                .env(TEST_KEY, value);
            assert_eq!(probe_output(&mut parent)?, Some(value.into()));
        }
        Ok(())
    }

    #[test]
    fn child_environment_probe() -> Result<()> {
        match env::var(TEST_MODE).as_deref() {
            Ok("probe") => println!(
                "{PROBE_PREFIX}{}",
                serde_json::to_string(&env::var(TEST_KEY).ok())?
            ),
            Ok("inherited") => {
                let defaults = defaults(json!({(TEST_KEY): "default"}))?;
                let mut child = child_command("probe")?;
                defaults.apply(&mut child);
                println!(
                    "{PROBE_PREFIX}{}",
                    serde_json::to_string(&probe_output(&mut child)?)?
                );
            }
            _ => {}
        }
        Ok(())
    }
}
