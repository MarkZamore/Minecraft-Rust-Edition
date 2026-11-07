//! In-engine rendering adapter for the launcher's owned notice session.
//! No mod, game setting, window, focus handler or input event is involved.
#[path = "lanmc_notice_protocol.rs"]
mod protocol;
#[path = "lanmc_notice_raster.rs"]
mod raster;
#[path = "lanmc_notice_render.rs"]
mod render;
#[path = "lanmc_notice_state.rs"]
mod state;

use std::sync::Arc;

/// The callback travels with one encoded frame, including asynchronous presents.
pub(super) type PresentCallback = Box<dyn FnOnce() + Send>;

pub(super) struct Overlay {
    service: Option<Arc<state::Service>>,
    gpu: Option<render::Renderer>,
    encoded_count: Option<usize>,
}

impl Overlay {
    pub(super) fn new() -> Self {
        Self {
            service: state::service(),
            gpu: None,
            encoded_count: None,
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn draw(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        encoder: &mut wgpu::CommandEncoder,
        target: &wgpu::TextureView,
        size: [u32; 2],
        format: wgpu::TextureFormat,
    ) {
        self.encoded_count = None;
        let Some(service) = &self.service else {
            return;
        };
        if size.contains(&0) {
            return;
        }
        let Some(prepared) = service.prepared(size[0]) else {
            // The successful empty native UI frame still has a real heartbeat.
            // The worker marks readiness only after it has a complete snapshot.
            self.encoded_count = Some(0);
            return;
        };
        if prepared.cards.len() != prepared.snapshot.notices.len() {
            service.report_error("Notice snapshot and raster lengths differ");
            return;
        }
        if prepared.cards.is_empty() {
            self.encoded_count = Some(0);
            return;
        }
        let gpu = self
            .gpu
            .get_or_insert_with(|| render::Renderer::new(device, format));
        gpu.set_format(device, format);
        let cards: Vec<_> = prepared
            .cards
            .iter()
            .zip(&prepared.snapshot.notices)
            .map(|(bitmap, notice)| render::RenderCard {
                revision: prepared.revision,
                width: bitmap.width,
                height: bitmap.height,
                rgba: &bitmap.rgba,
                created_unix_ms: notice.created_unix_ms,
            })
            .collect();
        let style = &prepared.snapshot.style;
        let motion = render::Motion {
            dpi_scale: prepared.snapshot.dpi_scale,
            gap: style.gap,
            rise_ms: style.rise_ms,
            fade_ms: style.fade_ms,
            life_ms: style.life_ms,
        };
        match gpu.draw(
            device,
            queue,
            encoder,
            target,
            size,
            &cards,
            motion,
            state::unix_ms(),
        ) {
            Ok(count) => self.encoded_count = Some(count),
            Err(error) => service.report_error(&error),
        }
    }

    /// Captures this frame's count, not a later frame's changing shared state.
    pub(super) fn callback(&self) -> Option<PresentCallback> {
        let count = self.encoded_count?;
        let service = self.service.clone()?;
        Some(Box::new(move || service.report_presented(count)))
    }
}
