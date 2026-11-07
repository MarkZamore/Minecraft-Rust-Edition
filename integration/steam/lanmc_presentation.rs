//! The pack's window presentation comes from its generated catalog metadata.
use std::sync::OnceLock;
use winit::window::{Icon, WindowAttributes};

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

/// Windows uses separate icons for the title bar and taskbar, including split windows.
pub(crate) fn with_icon(attributes: WindowAttributes) -> WindowAttributes {
    let icon = icon();
    let attributes = attributes.with_window_icon(icon.clone());
    #[cfg(target_os = "windows")]
    let attributes =
        winit::platform::windows::WindowAttributesExtWindows::with_taskbar_icon(attributes, icon);
    attributes
}
