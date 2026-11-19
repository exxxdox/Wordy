use wordy::wheel::{
    POLL_MS, Point, Rect, WheelHover, WheelState, picker_slot_at, wheel_label_text,
};

const SCREEN: Rect = Rect::new(0.0, 0.0, 1_000.0, 800.0);
const ORIGIN: Point = Point::new(500.0, 400.0);
const LABELS: [&str; 8] = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"];

fn open(wheel: &mut WheelState, origin: Point, screen: Rect) {
    assert!(wheel.open(0, origin, screen, &LABELS, Some(0x77)));
}

#[test]
fn north_is_first_and_sectors_advance_clockwise() {
    let mut wheel = WheelState::default();
    open(&mut wheel, ORIGIN, SCREEN);
    for (index, (dx, dy)) in [
        (0, -160),
        (120, -120),
        (160, 0),
        (120, 120),
        (0, 160),
        (-120, 120),
        (-160, 0),
        (-120, -120),
    ]
    .into_iter()
    .enumerate()
    {
        wheel.pointer_moved(
            POLL_MS * (index as u64 + 1),
            Point::new(500.0 + f64::from(dx), 400.0 + f64::from(dy)),
        );
        assert_eq!(wheel.selected(), Some(index));
    }
    wheel.pointer_moved(144, Point::new(575.0, 400.0));
    assert_eq!(wheel.selected(), None);
    wheel.pointer_moved(160, Point::new(576.0, 400.0));
    assert_eq!(wheel.selected(), Some(2));
    wheel.pointer_moved(176, Point::new(1_500.0, 400.0));
    assert_eq!(wheel.selected(), Some(2));
}

#[test]
fn clamping_moves_the_canvas_but_leaves_the_gesture_origin_unchanged() {
    let mut wheel = WheelState::default();
    let origin = Point::new(-1_980.0, 790.0);
    let screen = Rect::new(-2_000.0, 0.0, 800.0, 800.0);
    open(&mut wheel, origin, screen);
    assert_eq!(wheel.position(), Point::new(-2_000.0, 260.0));
    assert_eq!(wheel.anchor(), Some(Point::new(20.0, 530.0)));
    assert_eq!(wheel.poll(16, origin, true, false), None);
    assert_eq!(wheel.selected(), None);
    assert_eq!(
        wheel.poll(32, Point::new(-1_820.0, 790.0), false, false),
        Some(2)
    );
    assert_eq!(wheel.anchor(), None);
}

#[test]
fn dpi_conversion_scales_relative_to_the_monitor_origin() {
    let mut wheel = WheelState::default();
    let scale = 1.5;
    let monitor = Point::new(-2_560.0, -720.0);
    let screen = Rect::new(-2_560.0, -690.0, 2_560.0, 1_440.0).to_logical(monitor, scale);
    let physical_origin = Point::new(-2_539.0, -630.0);
    open(
        &mut wheel,
        physical_origin.to_logical(monitor, scale),
        screen,
    );
    assert_eq!(
        wheel.position().to_physical(monitor, scale),
        Point::new(-2_560.0, -690.0)
    );
    assert_eq!(wheel.anchor(), Some(Point::new(14.0, 40.0)));
    let before_dead_zone = Point::new(-2_426.0, -630.0).to_logical(monitor, scale);
    wheel.pointer_moved(16, before_dead_zone);
    assert_eq!(wheel.selected(), None);
    let east = Point::new(-2_425.0, -630.0).to_logical(monitor, scale);
    assert_eq!(wheel.poll(32, east, false, false), Some(2));
}

#[test]
fn click_uses_the_canvas_center_while_pointer_motion_uses_the_gesture_origin() {
    let mut wheel = WheelState::default();
    open(&mut wheel, Point::new(30.0, 40.0), SCREEN);
    wheel.pointer_moved(16, Point::new(190.0, 40.0));
    assert_eq!(wheel.selected(), Some(2));
    assert_eq!(wheel.click(17, Point::new(317.0, 430.0)), Some(4));
    assert_eq!(wheel.click(18, Point::new(317.0, 430.0)), None);
}

#[test]
fn release_dispatches_once_even_while_fading_and_second_open_waits_for_release() {
    let mut wheel = WheelState::default();
    open(&mut wheel, ORIGIN, SCREEN);
    assert_eq!(wheel.opacity(0), 0.0);
    assert!(wheel.is_visible(0));
    assert!(!wheel.open(20, ORIGIN, SCREEN, &["别的轮盘"], Some(0x78)));
    assert_eq!(wheel.key(), Some(0x77));
    assert_eq!(wheel.opacity(60), 0.875);
    assert_eq!(wheel.opacity(120), 1.0);
    let east = Point::new(660.0, 400.0);
    assert_eq!(wheel.poll(120, east, false, false), Some(2));
    assert!(!wheel.is_active());
    assert_eq!(wheel.poll(121, east, false, false), None);
    assert_eq!(wheel.opacity(165), 0.125);
    assert!(!wheel.is_visible(210));
}

#[test]
fn reopen_retargets_from_the_current_opacity_and_cancel_ends_the_animation() {
    let mut wheel = WheelState::default();
    open(&mut wheel, ORIGIN, SCREEN);
    wheel.dismiss(120);
    let fading = wheel.opacity(165);
    assert!(wheel.open(165, ORIGIN, SCREEN, &["新轮盘"], None));
    assert_eq!(wheel.opacity(165), fading);
    assert_eq!(wheel.labels()[0], "新轮盘");
    assert!(wheel.is_visible(210));
    assert!(wheel.is_active());
    assert_eq!(wheel.opacity(285), 1.0);
    wheel.cancel();
    assert_eq!(wheel.opacity(286), 0.0);
    assert_eq!(wheel.selected(), None);
    assert!(!wheel.is_visible(286));
}

#[test]
fn escape_and_right_click_take_precedence_over_release() {
    let mut wheel = WheelState::default();
    open(&mut wheel, ORIGIN, SCREEN);
    let east = Point::new(660.0, 400.0);
    assert_eq!(wheel.poll(16, east, true, false), None);
    assert_eq!(wheel.selected(), Some(2));
    assert_eq!(wheel.poll(32, east, false, true), None);
    assert!(!wheel.is_active());
}

#[test]
fn empty_slots_have_no_highlight_or_playback_and_no_bound_key_means_no_release() {
    let mut wheel = WheelState::default();
    assert!(wheel.open(0, ORIGIN, SCREEN, &["  北  ", "  "], None));
    assert_eq!(wheel.labels()[0], "北");
    assert_eq!(wheel.labels()[1], "");
    assert_eq!(wheel.labels().len(), 8);
    assert_eq!(wheel.poll(16, Point::new(660.0, 400.0), false, false), None);
    assert_eq!(wheel.selected(), Some(2));
    assert_eq!(wheel.highlights(106), [0.0; 8]);
    assert_eq!(wheel.click(106, Point::new(477.0, 270.0)), None);
    assert!(!wheel.is_active());
}

#[test]
fn picker_shares_directions_but_limits_hits_to_the_scaled_ring() {
    for size in [280.0, 400.0] {
        let middle = size / 2.0;
        let scale = size / 634.0;
        assert_eq!(picker_slot_at(Point::new(middle, middle), size), None);
        assert_eq!(
            picker_slot_at(Point::new(middle, middle - 160.0 * scale), size),
            Some(0)
        );
        assert_eq!(
            picker_slot_at(Point::new(middle + 160.0 * scale, middle), size),
            Some(2)
        );
        assert_eq!(
            picker_slot_at(Point::new(middle + 233.0 * scale, middle), size),
            None
        );
    }
}

#[test]
fn retargeted_hover_uses_the_current_frame() {
    let mut hover = WheelHover::default();
    hover.select(Some(0), 0);
    let first = hover.values(45);
    assert_eq!(first[0], 0.875);
    hover.select(Some(2), 45);
    assert_eq!(hover.values(45), first);
    assert_eq!(hover.values(135)[0], 0.0);
    assert_eq!(hover.values(135)[2], 1.0);
    hover.select(None, 135);
    assert_eq!(hover.values(225), [0.0; 8]);
}

#[test]
fn label_truncation_matches_saved_behavior() {
    assert_eq!(
        wheel_label_text("一二三四五六七八九十"),
        "一二三四五六七八九十"
    );
    assert_eq!(
        wheel_label_text("一二三四五六七八九十😀"),
        "一二三四五六七八九十..."
    );
}
