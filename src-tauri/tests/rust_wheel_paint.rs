#![cfg(windows)]

use wordy::wheel_paint::{WheelBitmap, WheelFrame, WheelRenderer};

fn pixel(bitmap: &WheelBitmap, x: usize, y: usize) -> [u8; 4] {
    let offset = (y * bitmap.width as usize + x) * 4;
    bitmap.pixels[offset..offset + 4].try_into().unwrap()
}

#[test]
fn paints_the_game_wheel_on_a_transparent_canvas() {
    let renderer = WheelRenderer::new().unwrap();
    let labels = std::array::from_fn(|_| String::from("一二三四五六七八九十"));
    let bitmap = renderer.render(WheelFrame::new(&labels), 0.5).unwrap();
    assert_eq!((bitmap.width, bitmap.height), (317, 270));
    assert_eq!(pixel(&bitmap, 0, 0), [0; 4]);
    assert!(
        bitmap
            .pixels
            .as_chunks::<4>()
            .0
            .iter()
            .all(|p| p[..3].iter().all(|c| *c <= p[3]))
    );
    assert!(bitmap.pixels.as_chunks::<4>().0.iter().any(|p| p[3] > 200));
}

#[test]
fn preview_bytes_carry_dimensions_and_the_native_frame() {
    let renderer = WheelRenderer::new().unwrap();
    let labels = std::array::from_fn(|_| String::new());
    let bytes = renderer
        .render_preview(&labels, Some(0), "Segoe UI Variable", 0.5)
        .unwrap();
    let width = u32::from_le_bytes(bytes[..4].try_into().unwrap());
    let height = u32::from_le_bytes(bytes[4..8].try_into().unwrap());
    assert_eq!((width, height), (317, 270));
    assert_eq!(bytes.len(), 8 + 317 * 270 * 4);
    // 预览与运行时共用同一绘制路径：选中非空槽时指针出现在东侧。
    let mut labels = std::array::from_fn(|_| String::new());
    labels[2] = "下一项".to_owned();
    let bytes = renderer
        .render_preview(&labels, Some(2), "Segoe UI Variable", 1.0)
        .unwrap();
    let alpha = |x: usize, y: usize| bytes[8 + (y * 634 + x) * 4 + 3];
    assert!(alpha(405, 270) > 170);
}

#[test]
fn preview_highlights_an_empty_selected_slot() {
    let renderer = WheelRenderer::new().unwrap();
    let labels = std::array::from_fn(|_| String::new());
    let selected = renderer
        .render_preview(&labels, Some(3), "Segoe UI Variable", 0.5)
        .unwrap();
    let none = renderer
        .render_preview(&labels, None, "Segoe UI Variable", 0.5)
        .unwrap();
    // 空槽是录制保存时的常见目标，选中后必须有可见变化。
    assert_ne!(selected, none);
}

#[test]
fn game_pointer_belongs_only_to_the_current_playable_sector() {
    let renderer = WheelRenderer::new().unwrap();
    let mut labels = std::array::from_fn(|_| String::new());
    labels[0] = "上一项".to_owned();
    labels[2] = "下一项".to_owned();
    let mut frame = WheelFrame::new(&labels);
    frame.selected = Some(2);
    frame.highlights = Some([0.70, 0.0, 0.90, 0.0, 0.0, 0.0, 0.0, 0.0]);
    let east = renderer.render(frame, 1.0).unwrap();
    assert!(pixel(&east, 405, 270)[3] > 170);
    assert!(pixel(&east, 317, 182)[3] < 170);
    frame.selected = Some(1);
    let empty = renderer.render(frame, 1.0).unwrap();
    assert!(pixel(&empty, 405, 270)[3] < 170);
}

#[test]
fn game_picker_knob_stays_with_selected_slot_while_hovering_elsewhere() {
    let renderer = WheelRenderer::new().unwrap();
    let labels = std::array::from_fn(|_| String::new());
    let mut frame = WheelFrame::new(&labels);
    frame.editing = true;
    frame.selected = Some(0);
    frame.highlights = Some([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]);
    let selected = renderer.render(frame, 1.0).unwrap();
    frame.highlights = Some([1.0, 0.45, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]);
    let hovered = renderer.render(frame, 1.0).unwrap();
    assert_ne!(
        selected.pixels, hovered.pixels,
        "hover still highlights the sector"
    );
    assert!(
        (232..309).all(|y| (287..348).all(|x| pixel(&selected, x, y) == pixel(&hovered, x, y))),
        "hover must leave the central knob unchanged"
    );
}

#[test]
fn configured_font_changes_the_rendered_labels() {
    let renderer = WheelRenderer::new().unwrap();
    let labels = std::array::from_fn(|_| String::from("MMMM"));
    let mut first = WheelFrame::new(&labels);
    first.font = "Segoe UI Variable";
    let mut second = first;
    second.font = "Consolas";
    assert_ne!(
        renderer.render(first, 1.0).unwrap().pixels,
        renderer.render(second, 1.0).unwrap().pixels
    );
}
