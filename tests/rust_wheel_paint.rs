#![cfg(windows)]

use wordy::wheel::WheelStyle;
use wordy::wheel_paint::{WheelBitmap, WheelFrame, WheelRenderer};

fn pixel(bitmap: &WheelBitmap, x: usize, y: usize) -> [u8; 4] {
    let offset = (y * bitmap.width as usize + x) * 4;
    bitmap.pixels[offset..offset + 4].try_into().unwrap()
}

#[test]
fn paints_four_distinct_styles_on_the_same_transparent_canvas() {
    let renderer = WheelRenderer::new().unwrap();
    let labels = std::array::from_fn(|_| String::from("一二三四五六七八九十"));
    let styles = [
        WheelStyle::Classic,
        WheelStyle::Glass,
        WheelStyle::Petals,
        WheelStyle::Game,
    ];
    let mut textures = Vec::new();
    for style in styles {
        let bitmap = renderer
            .render(WheelFrame::new(&labels, style), 0.5)
            .unwrap();
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
        assert!(!textures.contains(&bitmap.pixels));
        textures.push(bitmap.pixels);
    }
}

#[test]
fn game_pointer_belongs_only_to_the_current_playable_sector() {
    let renderer = WheelRenderer::new().unwrap();
    let mut labels = std::array::from_fn(|_| String::new());
    labels[0] = "上一项".to_owned();
    labels[2] = "下一项".to_owned();
    let mut frame = WheelFrame::new(&labels, WheelStyle::Game);
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
    let mut frame = WheelFrame::new(&labels, WheelStyle::Game);
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
fn classic_shadow_fades_outside_the_disc() {
    let renderer = WheelRenderer::new().unwrap();
    let labels = std::array::from_fn(|_| String::new());
    let bitmap = renderer
        .render(WheelFrame::new(&labels, WheelStyle::Classic), 1.0)
        .unwrap();
    let outer = pixel(&bitmap, 317, 8)[3];
    let middle = pixel(&bitmap, 317, 15)[3];
    let inner = pixel(&bitmap, 317, 22)[3];
    assert_eq!(outer, 0);
    assert!(inner > middle && middle > outer);
}
