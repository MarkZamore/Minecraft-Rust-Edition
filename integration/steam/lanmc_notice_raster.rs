//! Safe bridge from the bounded launcher protocol to the audited platform rasterizer.
//! All Windows FFI lives in lanmc_notice_platform, never in the game/render crate.
use super::protocol::Snapshot;
pub use lanmc_notice_platform::CardBitmap;

pub fn rasterize(snapshot: &Snapshot, viewport_width: u32) -> Result<Vec<CardBitmap>, String> {
    let source = &snapshot.style;
    let converted = lanmc_notice_platform::Snapshot {
        style: lanmc_notice_platform::Style {
            bg_argb: source.bg_argb,
            border_argb: source.border_argb,
            text_argb: source.text_argb,
            muted_argb: source.muted_argb,
            font_family: source.font_family.clone(),
            font_size: source.font_size,
            width: source.width,
            gap: source.gap,
            padding: source.padding,
            radius: source.radius,
            rise_ms: source.rise_ms,
            fade_ms: source.fade_ms,
            life_ms: source.life_ms,
        },
        notices: snapshot
            .notices
            .iter()
            .map(|notice| lanmc_notice_platform::Notice {
                title: notice.title.clone(),
                body: notice.body.clone(),
                created_unix_ms: notice.created_unix_ms,
            })
            .collect(),
        dpi_scale: snapshot.dpi_scale,
    };
    lanmc_notice_platform::rasterize(&converted, viewport_width)
}
