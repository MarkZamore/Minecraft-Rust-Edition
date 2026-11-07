//! Launcher-styled notice cards, rasterized in memory using Windows fonts.
//!
//! No window, input, desktop DC or game font atlas is created or changed.
//! GDI contributes grayscale glyph coverage only; the result is straight RGBA.
use super::protocol::{Snapshot, Style};

const MAX_SIDE: u32 = 4096;
const MAX_TOTAL_BYTES: usize = 64 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct CardBitmap {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

#[derive(Clone, Copy, Debug)]
struct Geometry {
    width: u32,
    height: u32,
    padding: u32,
    font: i32,
    line: u32,
    between: u32,
    radius: f32,
}

fn geometry(snapshot: &Snapshot, viewport_width: u32) -> Result<Geometry, String> {
    let s = &snapshot.style;
    if !snapshot.dpi_scale.is_finite()
        || !(0.5..=4.0).contains(&snapshot.dpi_scale)
        || [s.width, s.padding, s.font_size, s.radius, s.gap]
            .iter()
            .any(|n| !n.is_finite() || *n < 0.0)
        || s.font_family.is_empty()
        || s.font_family.chars().any(char::is_control)
        || snapshot.notices.len() > 3
        || snapshot.notices.iter().any(|notice| {
            notice.title.encode_utf16().count() > 160
                || notice.body.encode_utf16().count() > 320
                || notice.title.contains('\0')
                || notice.body.contains('\0')
        })
    {
        return Err("Invalid notice raster dimensions or font".into());
    }
    let dpi = snapshot.dpi_scale as f64;
    let width = (s.width as f64 * dpi)
        .round()
        .min((viewport_width as f64 - 2.0 * (s.gap as f64 * dpi).round()).max(1.0))
        .max(1.0);
    let padding = (s.padding as f64 * dpi).round().max(1.0);
    let font = (s.font_size as f64 * dpi).round().max(8.0);
    let line = (font * 1.45).round();
    let between = (4.0 * dpi).round();
    let height = 2.0 * padding + 2.0 * line + between;
    if width > MAX_SIDE as f64
        || height > MAX_SIDE as f64
        || font > MAX_SIDE as f64
        // The output cards and one reusable GDI mask are live together.
        || width * height * 4.0 * (snapshot.notices.len() + 1) as f64 > MAX_TOTAL_BYTES as f64
    {
        return Err("Notice raster exceeds its bounded canvas".into());
    }
    Ok(Geometry {
        width: width as u32,
        height: height as u32,
        padding: padding as u32,
        font: font as i32,
        line: line as u32,
        between: between as u32,
        radius: (s.radius * snapshot.dpi_scale)
            .min(width as f32 / 2.0)
            .min(height as f32 / 2.0),
    })
}

fn channels(argb: u32) -> [f32; 4] {
    [
        ((argb >> 16) & 255) as f32 / 255.0,
        ((argb >> 8) & 255) as f32 / 255.0,
        (argb & 255) as f32 / 255.0,
        (argb >> 24) as f32 / 255.0,
    ]
}

fn over(pixel: &mut [u8], argb: u32, coverage: f32) {
    let color = channels(argb);
    let source_alpha = color[3] * coverage.clamp(0.0, 1.0);
    let dest_alpha = pixel[3] as f32 / 255.0;
    let alpha = source_alpha + dest_alpha * (1.0 - source_alpha);
    if alpha == 0.0 {
        pixel.fill(0);
        return;
    }
    for i in 0..3 {
        pixel[i] = ((color[i] * source_alpha
            + pixel[i] as f32 / 255.0 * dest_alpha * (1.0 - source_alpha))
            / alpha
            * 255.0)
            .round()
            .clamp(0.0, 255.0) as u8;
    }
    pixel[3] = (alpha * 255.0).round().clamp(0.0, 255.0) as u8;
}

/// Antialias the signed distance of a rounded rectangle over one physical pixel.
fn rounded_coverage(x: f32, y: f32, width: f32, height: f32, radius: f32) -> f32 {
    if width <= 0.0 || height <= 0.0 {
        return 0.0;
    }
    let radius = radius.max(0.0).min(width / 2.0).min(height / 2.0);
    let qx = (x - width / 2.0).abs() - width / 2.0 + radius;
    let qy = (y - height / 2.0).abs() - height / 2.0 + radius;
    let distance = qx.max(0.0).hypot(qy.max(0.0)) + qx.max(qy).min(0.0) - radius;
    (0.5 - distance).clamp(0.0, 1.0)
}

fn background(style: &Style, shape: Geometry) -> CardBitmap {
    let mut result = CardBitmap {
        width: shape.width,
        height: shape.height,
        rgba: vec![0; shape.width as usize * shape.height as usize * 4],
    };
    for y in 0..shape.height {
        for x in 0..shape.width {
            let fx = x as f32 + 0.5;
            let fy = y as f32 + 0.5;
            let outer = rounded_coverage(
                fx,
                fy,
                shape.width as f32,
                shape.height as f32,
                shape.radius,
            );
            let inner = rounded_coverage(
                fx - 1.0,
                fy - 1.0,
                shape.width as f32 - 2.0,
                shape.height as f32 - 2.0,
                shape.radius - 1.0,
            );
            let at = (y as usize * shape.width as usize + x as usize) * 4;
            let pixel = &mut result.rgba[at..at + 4];
            over(pixel, style.bg_argb, outer);
            over(pixel, style.border_argb, (outer - inner).clamp(0.0, 1.0));
        }
    }
    result
}

#[cfg(windows)]
pub fn rasterize(snapshot: &Snapshot, viewport_width: u32) -> Result<Vec<CardBitmap>, String> {
    let shape = geometry(snapshot, viewport_width)?;
    if snapshot.notices.is_empty() {
        return Ok(Vec::new());
    }
    let mut mask = windows::Mask::new(shape.width, shape.height)?;
    let mut cards = Vec::with_capacity(snapshot.notices.len());
    for notice in &snapshot.notices {
        let mut card = background(&snapshot.style, shape);
        mask.line(
            &notice.title,
            &snapshot.style.font_family,
            shape.font,
            true,
            [
                shape.padding,
                shape.padding,
                shape.width.saturating_sub(shape.padding),
                shape.padding + shape.line,
            ],
        )?;
        mask.composite(&mut card.rgba, snapshot.style.text_argb)?;
        let top = shape.padding + shape.line + shape.between;
        mask.line(
            &notice.body,
            &snapshot.style.font_family,
            shape.font,
            false,
            [
                shape.padding,
                top,
                shape.width.saturating_sub(shape.padding),
                top + shape.line,
            ],
        )?;
        mask.composite(&mut card.rgba, snapshot.style.muted_argb)?;
        cards.push(card);
    }
    Ok(cards)
}

#[cfg(not(windows))]
pub fn rasterize(_snapshot: &Snapshot, _viewport_width: u32) -> Result<Vec<CardBitmap>, String> {
    Err("Native launcher notice fonts require Windows".into())
}

#[cfg(windows)]
mod windows {
    use super::over;
    use std::{ffi::c_void, ptr};

    type Handle = *mut c_void;
    #[repr(C)]
    struct BitmapInfoHeader {
        size: u32,
        width: i32,
        height: i32,
        planes: u16,
        bit_count: u16,
        compression: u32,
        size_image: u32,
        x_pixels_per_meter: i32,
        y_pixels_per_meter: i32,
        colors_used: u32,
        colors_important: u32,
    }
    #[repr(C)]
    struct BitmapInfo {
        header: BitmapInfoHeader,
        colors: [u32; 1],
    }
    #[repr(C)]
    struct Rect {
        left: i32,
        top: i32,
        right: i32,
        bottom: i32,
    }

    #[link(name = "gdi32")]
    unsafe extern "system" {
        fn CreateCompatibleDC(dc: Handle) -> Handle;
        fn DeleteDC(dc: Handle) -> i32;
        fn CreateDIBSection(
            dc: Handle,
            info: *const BitmapInfo,
            usage: u32,
            bits: *mut *mut c_void,
            section: Handle,
            offset: u32,
        ) -> Handle;
        fn SelectObject(dc: Handle, object: Handle) -> Handle;
        fn DeleteObject(object: Handle) -> i32;
        fn CreateFontW(
            height: i32,
            width: i32,
            escapement: i32,
            orientation: i32,
            weight: i32,
            italic: u32,
            underline: u32,
            strikeout: u32,
            charset: u32,
            output_precision: u32,
            clipping_precision: u32,
            quality: u32,
            pitch_and_family: u32,
            face: *const u16,
        ) -> Handle;
        fn GetTextFaceW(dc: Handle, count: i32, face: *mut u16) -> i32;
        fn SetBkMode(dc: Handle, mode: i32) -> i32;
        fn SetTextColor(dc: Handle, color: u32) -> u32;
        fn GdiFlush() -> i32;
    }
    #[link(name = "user32")]
    unsafe extern "system" {
        fn DrawTextW(dc: Handle, text: *const u16, count: i32, rect: *mut Rect, format: u32)
        -> i32;
    }

    struct Font(Handle);
    impl Font {
        fn new(family: &str, size: i32, bold: bool) -> Result<Self, String> {
            let face: Vec<u16> = family.encode_utf16().chain(Some(0)).collect();
            // A negative height is the requested glyph em in physical pixels.
            // ANTIALIASED_QUALITY gives grayscale, never ClearType colour fringes.
            let font = unsafe {
                CreateFontW(
                    -size,
                    0,
                    0,
                    0,
                    if bold { 700 } else { 400 },
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    4,
                    0,
                    face.as_ptr(),
                )
            };
            if font.is_null() {
                return Err("Could not create the launcher notice font".into());
            }
            Ok(Self(font))
        }
    }
    impl Drop for Font {
        fn drop(&mut self) {
            unsafe {
                DeleteObject(self.0);
            }
        }
    }

    struct Selected {
        dc: Handle,
        previous: Handle,
    }
    impl Selected {
        fn new(dc: Handle, object: Handle) -> Result<Self, String> {
            let previous = unsafe { SelectObject(dc, object) };
            if previous.is_null() || previous as isize == -1 {
                return Err("Could not select a launcher notice graphics object".into());
            }
            Ok(Self { dc, previous })
        }
    }
    impl Drop for Selected {
        fn drop(&mut self) {
            unsafe {
                SelectObject(self.dc, self.previous);
            }
        }
    }

    pub(super) struct Mask {
        dc: Handle,
        bitmap: Handle,
        previous_bitmap: Handle,
        bits: *mut u8,
        bytes: usize,
    }
    impl Mask {
        pub(super) fn new(width: u32, height: u32) -> Result<Self, String> {
            let bytes = width as usize * height as usize * 4;
            let info = BitmapInfo {
                header: BitmapInfoHeader {
                    size: std::mem::size_of::<BitmapInfoHeader>() as u32,
                    width: width as i32,
                    height: -(height as i32),
                    planes: 1,
                    bit_count: 32,
                    compression: 0,
                    size_image: bytes as u32,
                    x_pixels_per_meter: 0,
                    y_pixels_per_meter: 0,
                    colors_used: 0,
                    colors_important: 0,
                },
                colors: [0],
            };
            let dc = unsafe { CreateCompatibleDC(ptr::null_mut()) };
            if dc.is_null() {
                return Err("Could not create a launcher notice memory context".into());
            }
            let mut bits = ptr::null_mut();
            let bitmap = unsafe { CreateDIBSection(dc, &info, 0, &mut bits, ptr::null_mut(), 0) };
            if bitmap.is_null() || bits.is_null() {
                unsafe {
                    if !bitmap.is_null() {
                        DeleteObject(bitmap);
                    }
                    DeleteDC(dc);
                }
                return Err("Could not allocate the launcher notice glyph mask".into());
            }
            let previous_bitmap = unsafe { SelectObject(dc, bitmap) };
            if previous_bitmap.is_null() || previous_bitmap as isize == -1 {
                unsafe {
                    DeleteObject(bitmap);
                    DeleteDC(dc);
                }
                return Err("Could not select the launcher notice glyph mask".into());
            }
            unsafe {
                SetBkMode(dc, 1);
                SetTextColor(dc, 0x00ff_ffff);
            }
            Ok(Self {
                dc,
                bitmap,
                previous_bitmap,
                bits: bits.cast(),
                bytes,
            })
        }

        pub(super) fn line(
            &mut self,
            text: &str,
            family: &str,
            size: i32,
            bold: bool,
            bounds: [u32; 4],
        ) -> Result<(), String> {
            // GDI batches must complete before direct access to DIB pixels.
            if unsafe { GdiFlush() } == 0 {
                return Err("Could not finish the previous launcher notice glyph batch".into());
            }
            unsafe {
                ptr::write_bytes(self.bits, 0, self.bytes);
            }
            if text.is_empty() || bounds[2] <= bounds[0] || bounds[3] <= bounds[1] {
                return Ok(());
            }
            let family = if family.encode_utf16().count() < 32 {
                family
            } else {
                "Segoe UI"
            };
            let mut font = Font::new(family, size, bold)?;
            let mut selected = Selected::new(self.dc, font.0)?;
            let mut actual = [0u16; 64];
            let len = unsafe { GetTextFaceW(self.dc, actual.len() as i32, actual.as_mut_ptr()) };
            let face = if len > 0 {
                String::from_utf16_lossy(&actual[..len as usize])
                    .trim_end_matches('\0')
                    .to_owned()
            } else {
                String::new()
            };
            if !face.eq_ignore_ascii_case(family) && !family.eq_ignore_ascii_case("Segoe UI") {
                // Match the launcher's documented system-font fallback.
                drop(selected);
                font = Font::new("Segoe UI", size, bold)?;
                selected = Selected::new(self.dc, font.0)?;
            }
            let text: String = text
                .chars()
                .map(|ch| if ch.is_control() { ' ' } else { ch })
                .collect();
            let text: Vec<u16> = text.encode_utf16().collect();
            let mut rect = Rect {
                left: bounds[0] as i32,
                top: bounds[1] as i32,
                right: bounds[2] as i32,
                bottom: bounds[3] as i32,
            };
            let result = unsafe {
                DrawTextW(
                    self.dc,
                    text.as_ptr(),
                    text.len() as i32,
                    &mut rect,
                    0x0020 | 0x0800 | 0x8000,
                )
            };
            let flushed = unsafe { GdiFlush() } != 0;
            drop(selected);
            drop(font);
            if result == 0 || !flushed {
                return Err("Could not rasterize launcher notice text".into());
            }
            Ok(())
        }

        pub(super) fn composite(&self, rgba: &mut [u8], color: u32) -> Result<(), String> {
            if rgba.len() != self.bytes || unsafe { GdiFlush() } == 0 {
                return Err("Invalid launcher notice mask or unfinished graphics batch".into());
            }
            let mask = unsafe { std::slice::from_raw_parts(self.bits, self.bytes) };
            for (pixel, coverage) in rgba.chunks_exact_mut(4).zip(mask.chunks_exact(4)) {
                let coverage = coverage[0].max(coverage[1]).max(coverage[2]) as f32 / 255.0;
                if coverage > 0.0 {
                    over(pixel, color, coverage);
                }
            }
            Ok(())
        }
    }
    impl Drop for Mask {
        fn drop(&mut self) {
            unsafe {
                GdiFlush();
                SelectObject(self.dc, self.previous_bitmap);
                DeleteObject(self.bitmap);
                DeleteDC(self.dc);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::protocol::Notice;
    use super::*;

    fn fixture(dpi_scale: f32) -> Snapshot {
        Snapshot {
            style: Style {
                bg_argb: 0xff1c_2230,
                border_argb: 0x40ff_ffff,
                text_argb: 0xffff_ffff,
                muted_argb: 0xffa0_b0c0,
                font_family: "Segoe UI Variable Text".into(),
                font_size: 14.0,
                width: 320.0,
                gap: 24.0,
                padding: 16.0,
                radius: 8.0,
                rise_ms: 200,
                fade_ms: 350,
                life_ms: 7000,
            },
            notices: vec![Notice {
                title: "Мир & игра".into(),
                body: "Steam notice 123".into(),
                created_unix_ms: 1,
            }],
            dpi_scale,
        }
    }

    #[test]
    fn argb_becomes_straight_rgba_without_red_blue_swap() {
        let mut pixel = [0; 4];
        over(&mut pixel, 0x80f0_4020, 1.0);
        assert_eq!(pixel, [240, 64, 32, 128]);
        over(&mut pixel, 0xff00_00ff, 0.0);
        assert_eq!(pixel, [240, 64, 32, 128]);
    }

    #[test]
    fn dpi_is_applied_once_and_width_is_clamped_to_the_viewport() {
        let one = geometry(&fixture(1.0), 2000).unwrap();
        let two = geometry(&fixture(2.0), 2000).unwrap();
        assert_eq!((one.width, two.width), (320, 640));
        assert_eq!((one.font, two.font), (14, 28));
        assert_eq!(geometry(&fixture(2.0), 500).unwrap().width, 404);
    }

    #[test]
    fn rounded_background_retains_transparent_corners_and_opaque_center() {
        let snapshot = fixture(1.0);
        let shape = geometry(&snapshot, 1000).unwrap();
        let card = background(&snapshot.style, shape);
        assert_eq!(&card.rgba[..4], &[0, 0, 0, 0]);
        let center = ((shape.height / 2 * shape.width + shape.width / 2) * 4) as usize;
        assert_eq!(&card.rgba[center..center + 4], &[28, 34, 48, 255]);
    }

    #[test]
    fn invalid_and_excessive_canvases_are_rejected_before_allocation() {
        let mut snapshot = fixture(f32::NAN);
        assert!(geometry(&snapshot, 1000).is_err());
        snapshot.dpi_scale = 4.0;
        snapshot.style.width = 4096.0;
        assert!(geometry(&snapshot, 20000).is_err());
        snapshot = fixture(1.0);
        snapshot.style.font_size = f32::INFINITY;
        assert!(geometry(&snapshot, 1000).is_err());
        snapshot = fixture(1.0);
        snapshot.notices[0].title = "x".repeat(161);
        assert!(rasterize(&snapshot, 1000).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn actual_windows_font_paints_nonempty_cyrillic_glyphs_without_a_window() {
        let snapshot = fixture(1.0);
        let shape = geometry(&snapshot, 1000).unwrap();
        let background = background(&snapshot.style, shape);
        let cards = rasterize(&snapshot, 1000).unwrap();
        assert_eq!(cards.len(), 1);
        assert!(
            cards[0]
                .rgba
                .iter()
                .zip(background.rgba)
                .any(|(a, b)| *a != b)
        );
        assert!(
            cards[0]
                .rgba
                .chunks_exact(4)
                .any(|pixel| pixel[0] > 220 && pixel[1] > 220 && pixel[2] > 220)
        );
    }

    #[cfg(windows)]
    #[test]
    fn unknown_font_uses_the_system_fallback_and_long_text_stays_in_bounds() {
        let mut snapshot = fixture(1.5);
        snapshot.style.font_family = "NoSuchLauncherTypeface".into();
        snapshot.notices[0].body = "длинный текст & ".repeat(20);
        let cards = rasterize(&snapshot, 400).unwrap();
        assert_eq!(cards[0].width, 328);
        assert_eq!(
            cards[0].rgba.len(),
            cards[0].width as usize * cards[0].height as usize * 4
        );
        assert_eq!(&cards[0].rgba[..4], &[0, 0, 0, 0]);
    }
}
