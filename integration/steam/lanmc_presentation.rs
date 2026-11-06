//! The pack's window presentation comes from its generated catalog metadata.
use std::sync::OnceLock;
use winit::window::Icon;

pub(crate) fn name() -> Option<&'static str> {
    static NAME: OnceLock<Option<String>> = OnceLock::new();
    NAME.get_or_init(|| {
        if !cfg!(target_os = "windows") {
            return None;
        }
        std::env::var("LANMINECRAFT_BUILD_NAME")
            .ok()
            .filter(|name| {
                !name.is_empty()
                    && name.len() <= 256
                    && name.trim() == name
                    && !name.chars().any(char::is_control)
            })
    })
    .as_deref()
}

pub(crate) fn title(fallback: impl Into<String>) -> String {
    name().map(str::to_owned).unwrap_or_else(|| fallback.into())
}

pub(crate) fn icon() -> Option<Icon> {
    if name().is_none() {
        return crate::window::icon();
    }
    let (width, height, pixels) =
        crate::hud::decode_bytes(include_bytes!("lanmc_rust_logo.png")).ok()?;
    Icon::from_rgba(pixels, width, height).ok()
}
