"""Exact Horizon 656 integration for the launcher's built-in native UI notices.

This augments the pack's existing mandatory source overlay. It is not a mod and
never changes the window, input, author graphics settings or game's GUI scale.
"""

from pathlib import Path


RENDER = "crates/horizon-client-render/src/"
RENDER_MANIFEST = "crates/horizon-client-render/Cargo.toml"
SOURCES = frozenset(RENDER + name for name in (
    "gfx.rs", "gfx/compositor.rs", "gfx/submit.rs", "gfx/frame_gen.rs",
    "gfx/gpu_culling.rs", "gfx/pack_center_depth.rs",
)) | {RENDER_MANIFEST}
COPY_FILES = (
    "lanmc_notices.rs", "lanmc_notice_protocol.rs", "lanmc_notice_state.rs",
    "lanmc_notice_raster.rs", "lanmc_notice_render.rs", "lanmc_notice_layout.rs",
)
PLATFORM_FILES = ("Cargo.toml", "src/lib.rs")


def apply(patched: dict[str, bytes], here: Path) -> None:
    def replace(path: str, old: str, new: str, count: int = 1) -> None:
        original = patched[path]
        before, after = old.encode("utf-8"), new.encode("utf-8")
        if original.count(before) != count:
            raise ValueError(f"Mandatory launcher-notice anchor missing or ambiguous in {path}: {old[:100]!r}")
        patched[path] = original.replace(before, after)

    def edit(name: str, old: str, new: str, count: int = 1) -> None:
        replace(RENDER + name, old, new, count)

    if not SOURCES.issubset(patched):
        raise ValueError("Mandatory launcher-notice source inputs are absent")
    replace("Cargo.toml", '    "crates/lanmc-steam-native",\n',
            '    "crates/lanmc-steam-native",\n    "crates/lanmc-notice-platform",\n')
    replace(RENDER_MANIFEST, "[dependencies]\n",
            '[dependencies]\nlanmc_notice_platform = { path = "../lanmc-notice-platform" }\n')
    edit("gfx.rs", "pub mod submit;", "pub mod submit;\nmod lanmc_notices;")
    edit("gfx.rs", "    first_frame: FirstFrame,\n",
         "    first_frame: FirstFrame,\n    lanmc_notices: lanmc_notices::Overlay,\n")
    edit("gfx.rs", "            display: DisplayWatch::default(),\n",
         "            display: DisplayWatch::default(),\n            lanmc_notices: lanmc_notices::Overlay::new(),\n")
    edit("gfx.rs", """        if overlay.real {
            self.own_timing = timing;
        }
        readback
""", """        // The composited window draws once after all panes; offscreen/scaled
        // captures are not a presented UI frame and must not refresh a heartbeat.
        if self.surface.is_some() {
            self.lanmc_notices.draw(
                &self.device, &self.queue, encoder, view_target,
                [self.config.width, self.config.height], self.frame_format,
            );
        }
        if overlay.real {
            self.own_timing = timing;
        }
        readback
""")

    # Queue acceptance is not presentation. Keep the token with its exact
    # encoded frame through either submission path and fire it after present.
    edit("gfx.rs", "let (encoder, surface_frame, after) = if readback.is_none()",
         "let (encoder, surface_frame, after, after_present) = if readback.is_none()")
    edit("gfx.rs", """                surface: surface_frame,
                after,
            };""", """                surface: surface_frame,
                after,
                after_present: self.lanmc_notices.callback(),
            };""")
    edit("gfx.rs", "Err(back) => (back.encoder, back.surface, back.after),",
         "Err(back) => (back.encoder, back.surface, back.after, back.after_present),")
    edit("gfx.rs", "            (encoder, surface_frame, None)\n",
         "            (encoder, surface_frame, None, self.lanmc_notices.callback())\n")
    edit("gfx.rs", "        let mut fg_made = false;\n",
         "        let mut fg_made = false;\n        let mut notice_generated_callback = None;\n")
    edit("gfx.rs", """                self.draw_overlay(&mut encoder, frame, &generated_view, generated, &overlay);
            }
        } else if scaled.is_some() {""", """                self.draw_overlay(&mut encoder, frame, &generated_view, generated, &overlay);
                notice_generated_callback = self.lanmc_notices.callback();
            } else if frame_gen::generated_only() && self.surface.is_some() {
                // The debug presenter may show the initial copied generated
                // frame too. Its launcher UI is independent of author HUD debug.
                self.lanmc_notices.draw(
                    &self.device, &self.queue, &mut encoder, &generated_view,
                    [self.config.width, self.config.height], self.frame_format,
                );
                notice_generated_callback = self.lanmc_notices.callback();
            }
        } else if scaled.is_some() {""")
    edit("gfx.rs", "            self.queue.present(surface_frame);\n",
         """            self.queue.present(surface_frame);
            if let Some(callback) = after_present {
                callback();
            }
""")
    edit("gfx.rs", "self.frame_gen.present(real, generated, fg_made);",
         """let after_present = if frame_gen::generated_only() {
                notice_generated_callback
            } else {
                after_present
            };
            self.frame_gen.present(real, generated, fg_made, after_present);""")

    edit("gfx/submit.rs", "    pub after: Option<AfterSubmit>,\n",
         """    pub after: Option<AfterSubmit>,
    /// Only after a real window surface was presented; never for offscreen work.
    pub after_present: Option<AfterSubmit>,
""")
    edit("gfx/submit.rs", """                    if let Some(surface) = job.surface {
                        queue.present(surface);
                    }
""", """                    if let Some(surface) = job.surface {
                        queue.present(surface);
                        if let Some(callback) = job.after_present {
                            callback();
                        }
                    }
""")
    edit("gfx/submit.rs", """                after: None,
            };""", """                after: None,
                after_present: None,
            };""")
    # These author GPU tests construct the same public submission envelope.
    # Preserve their compilation and their original offscreen semantics too.
    edit("gfx/gpu_culling.rs", """                        after,
                    };""", """                        after,
                        after_present: None,
                    };""")
    edit("gfx/pack_center_depth.rs", """                        after: Some(Box::new(after)),
                    };""", """                        after: Some(Box::new(after)),
                        after_present: None,
                    };""")
    edit("gfx/pack_center_depth.rs", """                after,
            };""", """                after,
                after_present: None,
            };""")

    edit("gfx/compositor.rs", "    suspended: bool,\n",
         "    suspended: bool,\n    lanmc_notices: super::lanmc_notices::Overlay,\n")
    edit("gfx/compositor.rs", "            suspended: !super::drawable_size(size[0], size[1], max),\n",
         """            suspended: !super::drawable_size(size[0], size[1], max),
            lanmc_notices: super::lanmc_notices::Overlay::new(),
""")
    edit("gfx/compositor.rs", """        self.queue.submit([encoder.finish()]);
        self.queue.present(frame);
        true
""", """        self.lanmc_notices.draw(
            &self.device, &self.queue, &mut encoder, &target,
            [self.config.width, self.config.height], self.config.format,
        );
        let after_present = self.lanmc_notices.callback();
        self.queue.submit([encoder.finish()]);
        self.queue.present(frame);
        if let Some(callback) = after_present {
            callback();
        }
        true
""")

    # Frame-generation's presenter owns the actual surface. A callback attached
    # to the real frame fires only if that frame was copied and presented.
    edit("gfx/frame_gen.rs", "    pub fn present(&mut self, real: wgpu::Texture, generated: wgpu::Texture, made: bool) {",
         """    pub fn present(
        &mut self, real: wgpu::Texture, generated: wgpu::Texture, made: bool,
        after_present: Option<Box<dyn FnOnce() + Send>>,
    ) {""")
    edit("gfx/frame_gen.rs", "                interval: self.interval,\n",
         "                interval: self.interval,\n                after_present,\n", count=3)
    edit("gfx/frame_gen.rs", "    interval: Duration,\n}\n\n/// The thread",
         "    interval: Duration,\n    after_present: Option<Box<dyn FnOnce() + Send>>,\n}\n\n/// The thread")
    edit("gfx/frame_gen.rs", """                let show = |texture: &wgpu::Texture| {
                    if failed_here.load(Ordering::Relaxed) {
                        return;
                    }
""", """                let show = |texture: &wgpu::Texture| {
                    if failed_here.load(Ordering::Relaxed) {
                        return false;
                    }
""")
    edit("gfx/frame_gen.rs", """                            failed_here.store(true, Ordering::Relaxed);
                            return;
                        }
                    };
""", """                            failed_here.store(true, Ordering::Relaxed);
                            return false;
                        }
                    };
""")
    edit("gfx/frame_gen.rs", """                    queue.present(target);
                };
""", """                    queue.present(target);
                    true
                };
""")
    edit("gfx/frame_gen.rs", """                    show(&pair.second);
                    let _ = give.send(pair.second);
""", """                    if show(&pair.second) {
                        if let Some(callback) = pair.after_present {
                            callback();
                        }
                    }
                    let _ = give.send(pair.second);
""")

    for filename in COPY_FILES:
        path = RENDER + "gfx/" + filename
        if path in patched:
            raise ValueError(f"Unexpected launcher-notice source already present: {path}")
        patched[path] = (here / filename).read_bytes()
    for filename in PLATFORM_FILES:
        path = "crates/lanmc-notice-platform/" + filename
        if path in patched:
            raise ValueError(f"Unexpected launcher-notice platform source already present: {path}")
        patched[path] = (here / "notice-platform" / filename).read_bytes()
