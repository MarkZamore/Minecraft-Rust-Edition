//! Physical-pixel layout for the launcher's final foreground notices.
//! Card bitmaps have already been rasterized at the effective DPI. Only the
//! outer gap and rise distance are converted here; game GUI scale is unrelated.

#[derive(Clone, Copy, Debug)]
pub(crate) struct Motion {
    pub dpi_scale: f32,
    pub gap: f32,
    pub rise_ms: u64,
    pub fade_ms: u64,
    pub life_ms: u64,
}

#[derive(Clone, Copy, Debug)]
pub(super) struct CardSize {
    pub width: u32,
    pub height: u32,
    pub created_unix_ms: u64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(super) struct Placement {
    pub index: usize,
    pub x: f32,
    pub y: f32,
    pub width: f32,
    pub height: f32,
    pub opacity: f32,
}

pub(super) fn layout(
    dimensions: [u32; 2],
    cards: &[CardSize],
    motion: Motion,
    now_ms: u64,
) -> Vec<Placement> {
    if dimensions.contains(&0)
        || cards.len() > 3
        || !motion.dpi_scale.is_finite()
        || !(0.5..=4.0).contains(&motion.dpi_scale)
        || !motion.gap.is_finite()
        || motion.gap < 0.0
        || motion.life_ms == 0
    {
        return Vec::new();
    }
    let [width, height] = dimensions.map(|n| n as f32);
    let gap = (motion.gap * motion.dpi_scale).round();
    let mut bottom = height - gap;
    let mut result = Vec::with_capacity(3);
    for (index, card) in cards.iter().enumerate().rev() {
        if card.width == 0 || card.height == 0 || card.created_unix_ms > now_ms {
            continue;
        }
        let age = now_ms - card.created_unix_ms;
        if age >= motion.life_ms {
            continue;
        }
        let rise = (age as f64 / motion.rise_ms.max(1) as f64).min(1.0) as f32;
        let fade = ((motion.life_ms - age) as f64 / motion.fade_ms.max(1) as f64).min(1.0) as f32;
        let opacity = rise.min(fade).clamp(0.0, 1.0);
        if opacity == 0.0 {
            continue;
        }
        if bottom <= 0.0 {
            break;
        }
        let x = gap.min((width - card.width as f32).max(0.0));
        let y = (bottom - card.height as f32).max(0.0);
        // Ease-out changes the rise distance, not the stack's resting layout.
        let ease = 1.0 - (1.0 - rise) * (1.0 - rise);
        result.push(Placement {
            index,
            x,
            y: y + gap * (1.0 - ease),
            width: card.width as f32,
            height: card.height as f32,
            opacity,
        });
        bottom = y - gap;
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn motion() -> Motion {
        Motion {
            dpi_scale: 1.5,
            gap: 16.0,
            rise_ms: 200,
            fade_ms: 350,
            life_ms: 7000,
        }
    }

    fn card(created_unix_ms: u64) -> CardSize {
        CardSize {
            width: 480,
            height: 120,
            created_unix_ms,
        }
    }

    #[test]
    fn newest_is_at_the_lower_left_with_physical_dimensions_unchanged() {
        let placed = layout([1920, 1080], &[card(1000), card(1100)], motion(), 2000);
        assert_eq!(placed.len(), 2);
        assert_eq!(placed[0].index, 1);
        assert_eq!((placed[0].x, placed[0].y), (24.0, 936.0));
        assert_eq!((placed[0].width, placed[0].height), (480.0, 120.0));
        assert_eq!(placed[1].y, 792.0);
        assert_eq!(placed[0].opacity, 1.0);
    }

    #[test]
    fn future_and_expired_cards_are_not_reported_as_rendered() {
        assert!(layout([1920, 1080], &[card(1000)], motion(), 999).is_empty());
        assert!(layout([1920, 1080], &[card(1000)], motion(), 1000).is_empty());
        assert!(layout([1920, 1080], &[card(1000)], motion(), 8000).is_empty());
    }

    #[test]
    fn rise_and_fade_are_bounded_and_do_not_shift_other_slots() {
        let rising = layout([1920, 1080], &[card(1000)], motion(), 1100);
        assert_eq!(rising[0].opacity, 0.5);
        assert_eq!(rising[0].y, 942.0);
        let fading = layout([1920, 1080], &[card(1000)], motion(), 7825);
        assert_eq!(fading[0].opacity, 0.5);
        assert_eq!(fading[0].y, 936.0);
    }

    #[test]
    fn small_surface_clips_without_negative_positions_or_overlapping_old_cards() {
        let placed = layout([240, 80], &[card(1000), card(1000)], motion(), 2000);
        assert_eq!(placed.len(), 1);
        assert_eq!((placed[0].x, placed[0].y), (0.0, 0.0));
        assert!(layout([0, 80], &[card(1000)], motion(), 2000).is_empty());
    }

    #[test]
    fn invalid_motion_and_more_than_three_cards_do_not_create_geometry() {
        let mut invalid = motion();
        invalid.dpi_scale = f32::NAN;
        assert!(layout([1920, 1080], &[card(1000)], invalid, 2000).is_empty());
        assert!(layout([1920, 1080], &[card(1000); 4], motion(), 2000).is_empty());
    }
}
