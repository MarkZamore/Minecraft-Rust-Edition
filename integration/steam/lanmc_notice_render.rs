//! A final native-UI pass, independent of the game's HUD, resource-pack font,
//! GUI scale and input state. All coordinates and card bitmaps are physical pixels.

#[path = "lanmc_notice_layout.rs"]
mod layout;
use crate::gfx::upload::Uploads;
pub(super) use layout::Motion;
use std::borrow::Cow;

pub(super) struct RenderCard<'a> {
    pub revision: u64,
    pub width: u32,
    pub height: u32,
    pub rgba: &'a [u8],
    pub created_unix_ms: u64,
}

#[repr(C)]
#[derive(Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
struct Vertex {
    clip: [f32; 2],
    uv: [f32; 2],
    opacity: f32,
}

struct CachedCard {
    revision: u64,
    size: [u32; 2],
    _texture: wgpu::Texture,
    bindings: wgpu::BindGroup,
}

pub(super) struct Renderer {
    pipeline: wgpu::RenderPipeline,
    layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    format: wgpu::TextureFormat,
    vertices: wgpu::Buffer,
    uploads: Uploads,
    cards: [Option<CachedCard>; 3],
}

impl Renderer {
    pub(super) fn new(device: &wgpu::Device, format: wgpu::TextureFormat) -> Self {
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("LANMinecraft notice texture layout"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("LANMinecraft notice sampler"),
            address_mode_u: wgpu::AddressMode::ClampToEdge,
            address_mode_v: wgpu::AddressMode::ClampToEdge,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        Self {
            pipeline: pipeline(device, &layout, format),
            layout,
            sampler,
            format,
            vertices: device.create_buffer(&wgpu::BufferDescriptor {
                label: Some("LANMinecraft notice vertices"),
                size: (18 * std::mem::size_of::<Vertex>()) as u64,
                usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            }),
            uploads: Uploads::with_chunk(device, 4096),
            cards: [None, None, None],
        }
    }

    pub(super) fn set_format(&mut self, device: &wgpu::Device, format: wgpu::TextureFormat) {
        if self.format != format {
            self.pipeline = pipeline(device, &self.layout, format);
            self.format = format;
        }
    }

    /// Encodes one pass after the final game UI. The returned count must be
    /// acknowledged to the launcher only after this frame was really presented.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn draw(
        &mut self,
        device: &wgpu::Device,
        _queue: &wgpu::Queue,
        encoder: &mut wgpu::CommandEncoder,
        target: &wgpu::TextureView,
        dimensions: [u32; 2],
        cards: &[RenderCard<'_>],
        motion: Motion,
        now_ms: u64,
    ) -> Result<usize, String> {
        if cards.len() > 3 {
            return Err("Notice image count exceeds three".into());
        }
        let sizes: Vec<_> = cards
            .iter()
            .map(|card| layout::CardSize {
                width: card.width,
                height: card.height,
                created_unix_ms: card.created_unix_ms,
            })
            .collect();
        let placed = layout::layout(dimensions, &sizes, motion, now_ms);
        if placed.is_empty() {
            self.cards = [None, None, None];
            return Ok(0);
        }
        let max_side = device.limits().max_texture_dimension_2d;
        // Validate every image before recording uploads so a rejected snapshot
        // cannot leave an unfinished staging-belt allocation in this encoder.
        for placement in &placed {
            let card = &cards[placement.index];
            let bytes = u64::from(card.width)
                .checked_mul(u64::from(card.height))
                .and_then(|pixels| pixels.checked_mul(4));
            if card.width > max_side
                || card.height > max_side
                || bytes != Some(card.rgba.len() as u64)
            {
                return Err("Invalid notice image dimensions or pixels".into());
            }
        }
        let mut vertices = Vec::with_capacity(18);
        for placement in &placed {
            let index = placement.index;
            let card = &cards[index];
            if self.cards[index].as_ref().is_none_or(|cached| {
                cached.revision != card.revision || cached.size != [card.width, card.height]
            }) {
                let texture = device.create_texture(&wgpu::TextureDescriptor {
                    label: Some("LANMinecraft notice card"),
                    size: wgpu::Extent3d {
                        width: card.width,
                        height: card.height,
                        depth_or_array_layers: 1,
                    },
                    mip_level_count: 1,
                    sample_count: 1,
                    dimension: wgpu::TextureDimension::D2,
                    // The HUD and Java notice renderer use raw UI colour bytes.
                    format: wgpu::TextureFormat::Rgba8Unorm,
                    usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
                    view_formats: &[],
                });
                self.uploads.texture(
                    encoder,
                    texture.as_image_copy(),
                    card.width,
                    card.height,
                    card.rgba,
                );
                let view = texture.create_view(&Default::default());
                let bindings = device.create_bind_group(&wgpu::BindGroupDescriptor {
                    label: Some("LANMinecraft notice card bindings"),
                    layout: &self.layout,
                    entries: &[
                        wgpu::BindGroupEntry {
                            binding: 0,
                            resource: wgpu::BindingResource::TextureView(&view),
                        },
                        wgpu::BindGroupEntry {
                            binding: 1,
                            resource: wgpu::BindingResource::Sampler(&self.sampler),
                        },
                    ],
                });
                self.cards[index] = Some(CachedCard {
                    revision: card.revision,
                    size: [card.width, card.height],
                    _texture: texture,
                    bindings,
                });
            }
            let x0 = placement.x / dimensions[0] as f32 * 2.0 - 1.0;
            let x1 = (placement.x + placement.width) / dimensions[0] as f32 * 2.0 - 1.0;
            let y0 = 1.0 - placement.y / dimensions[1] as f32 * 2.0;
            let y1 = 1.0 - (placement.y + placement.height) / dimensions[1] as f32 * 2.0;
            for (clip, uv) in [
                ([x0, y0], [0.0, 0.0]),
                ([x1, y0], [1.0, 0.0]),
                ([x1, y1], [1.0, 1.0]),
                ([x0, y0], [0.0, 0.0]),
                ([x1, y1], [1.0, 1.0]),
                ([x0, y1], [0.0, 1.0]),
            ] {
                vertices.push(Vertex {
                    clip,
                    uv,
                    opacity: placement.opacity,
                });
            }
        }
        self.uploads
            .buffer(encoder, &self.vertices, 0, bytemuck::cast_slice(&vertices));
        self.uploads.finish(encoder);
        let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("LANMinecraft final notices"),
            color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                view: target,
                resolve_target: None,
                depth_slice: None,
                ops: wgpu::Operations {
                    load: wgpu::LoadOp::Load,
                    store: wgpu::StoreOp::Store,
                },
            })],
            depth_stencil_attachment: None,
            timestamp_writes: None,
            occlusion_query_set: None,
            multiview_mask: None,
        });
        pass.set_pipeline(&self.pipeline);
        pass.set_vertex_buffer(0, self.vertices.slice(..));
        pass.set_viewport(
            0.0,
            0.0,
            dimensions[0] as f32,
            dimensions[1] as f32,
            0.0,
            1.0,
        );
        pass.set_scissor_rect(0, 0, dimensions[0], dimensions[1]);
        for (slot, placement) in placed.iter().enumerate() {
            pass.set_bind_group(
                0,
                &self.cards[placement.index]
                    .as_ref()
                    .expect("uploaded notice")
                    .bindings,
                &[],
            );
            let first = slot as u32 * 6;
            pass.draw(first..first + 6, 0..1);
        }
        Ok(placed.len())
    }
}

fn pipeline(
    device: &wgpu::Device,
    layout: &wgpu::BindGroupLayout,
    format: wgpu::TextureFormat,
) -> wgpu::RenderPipeline {
    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("LANMinecraft notice shader"),
        source: wgpu::ShaderSource::Wgsl(Cow::Borrowed(SHADER)),
    });
    let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("LANMinecraft notice pipeline layout"),
        bind_group_layouts: &[Some(layout)],
        ..Default::default()
    });
    let attributes = wgpu::vertex_attr_array![0 => Float32x2, 1 => Float32x2, 2 => Float32];
    device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("LANMinecraft final notice pipeline"),
        layout: Some(&pipeline_layout),
        vertex: wgpu::VertexState {
            module: &shader,
            entry_point: Some("vs_main"),
            compilation_options: Default::default(),
            buffers: &[Some(wgpu::VertexBufferLayout {
                array_stride: std::mem::size_of::<Vertex>() as u64,
                step_mode: wgpu::VertexStepMode::Vertex,
                attributes: &attributes,
            })],
        },
        primitive: Default::default(),
        depth_stencil: None,
        multisample: Default::default(),
        fragment: Some(wgpu::FragmentState {
            module: &shader,
            entry_point: Some("fs_main"),
            compilation_options: Default::default(),
            targets: &[Some(wgpu::ColorTargetState {
                format,
                blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                write_mask: wgpu::ColorWrites::ALL,
            })],
        }),
        multiview_mask: None,
        cache: None,
    })
}

const SHADER: &str = r#"
@group(0) @binding(0) var card: texture_2d<f32>;
@group(0) @binding(1) var card_sampler: sampler;
struct VertexOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) opacity: f32,
}
@vertex fn vs_main(@location(0) clip: vec2<f32>, @location(1) uv: vec2<f32>,
                  @location(2) opacity: f32) -> VertexOut {
    var out: VertexOut;
    out.clip = vec4(clip, 0.0, 1.0);
    out.uv = uv;
    out.opacity = opacity;
    return out;
}
@fragment fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let sampled = textureSample(card, card_sampler, in.uv);
    return vec4(sampled.rgb, sampled.a * in.opacity);
}
"#;
