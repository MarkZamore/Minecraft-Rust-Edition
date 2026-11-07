//! Pure contract, Windows font raster and heartbeat tests, without Minecraft.
#![allow(dead_code)]
#![forbid(unsafe_code)]
#[path = "../integration/steam/lanmc_notice_layout.rs"]
mod layout;
#[path = "../integration/steam/lanmc_notice_protocol.rs"]
mod protocol;
#[path = "../integration/steam/lanmc_notice_raster.rs"]
mod raster;
#[path = "../integration/steam/lanmc_notice_state.rs"]
mod state;

#[test]
fn accepted_future_notice_stays_pending_then_paints_and_expires_at_original_time() {
    let style = b"bgArgb=FF112233\nborderArgb=FF334455\ntextArgb=FFFFFFFF\nmutedArgb=FFEEEEEE\nfontFamily=Segoe UI\nfontSize=14\nwidth=320\ngap=24\npadding=16\nradius=8\nriseMs=200\nfadeMs=350\nlifeMs=7000\n";
    let snapshot = protocol::parse_snapshot(
        style,
        b"count=1\ndpiScale=1.5\n0.title=Future\n0.body=Still pending\n0.createdUnixMs=11500\n",
        10_000,
    )
    .unwrap();
    let card = layout::CardSize {
        width: 480,
        height: 120,
        created_unix_ms: snapshot.notices[0].created_unix_ms,
    };
    let motion = layout::Motion {
        dpi_scale: snapshot.dpi_scale,
        gap: snapshot.style.gap,
        rise_ms: snapshot.style.rise_ms,
        fade_ms: snapshot.style.fade_ms,
        life_ms: snapshot.style.life_ms,
    };
    // The same accepted card remains available; a no-draw frame never consumes it.
    assert!(layout::layout([1920, 1080], &[card], motion, 10_000).is_empty());
    assert!(layout::layout([1920, 1080], &[card], motion, 11_499).is_empty());
    assert_eq!(
        layout::layout([1920, 1080], &[card], motion, 11_501).len(),
        1
    );
    assert_eq!(
        layout::layout([1920, 1080], &[card], motion, 18_499).len(),
        1
    );
    assert!(layout::layout([1920, 1080], &[card], motion, 18_500).is_empty());
}
