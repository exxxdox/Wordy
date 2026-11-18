use super::*;

#[allow(clippy::too_many_arguments)]
pub(super) fn paint_capsule(
    hwnd: HWND,
    dc: HDC,
    dest: RECT,
    origin_x: i32,
    origin_y: i32,
    icon: Option<[u8; 3]>,
    gear: bool,
    transparent: bool,
) {
    let mut bounds = RECT::default();
    unsafe { GetClientRect(hwnd, &mut bounds) };
    let scale = bounds.bottom as f32 / HEIGHT as f32;
    let (width, height) = (dest.right - dest.left, dest.bottom - dest.top);
    if scale <= 0.0 || width <= 0 || height <= 0 {
        return;
    }
    let glow = capsule_glow(hwnd);
    let center = if gear { 514.0 } else { 460.0 };
    let mut pixels = Vec::with_capacity((width * height) as usize);
    for row in 0..height {
        let y = (origin_y as f32 + row as f32 + 0.5) / scale;
        for col in 0..width {
            let x = (origin_x as f32 + col as f32 + 0.5) / scale;
            let mut color = if transparent {
                [
                    (OVERLAY_KEY & 0xff) as u8,
                    ((OVERLAY_KEY >> 8) & 0xff) as u8,
                    ((OVERLAY_KEY >> 16) & 0xff) as u8,
                ]
            } else {
                capsule_color(x, y, glow)
            };
            if let Some(icon) = icon {
                let x = x - center;
                let y = y - 37.0;
                let subpixel = 0.25 / scale;
                let mut coverage = 0.0;
                for (dx, dy) in [
                    (-subpixel, -subpixel),
                    (subpixel, -subpixel),
                    (-subpixel, subpixel),
                    (subpixel, subpixel),
                ] {
                    if capsule_icon_at(x + dx, y + dy, gear) {
                        coverage += 0.25;
                    }
                }
                color = blend(color, icon, coverage);
            }
            pixels.push(dib_rgb(color));
        }
    }
    blit_pixels(dc, dest, &pixels);
}

pub(super) fn blit_pixels(dc: HDC, dest: RECT, pixels: &[u32]) {
    let (width, height) = (dest.right - dest.left, dest.bottom - dest.top);
    let info = BITMAPINFO {
        bmiHeader: BITMAPINFOHEADER {
            biSize: mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width,
            biHeight: -height,
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB,
            ..Default::default()
        },
        ..Default::default()
    };
    unsafe {
        StretchDIBits(
            dc,
            dest.left,
            dest.top,
            width,
            height,
            0,
            0,
            width,
            height,
            pixels.as_ptr().cast(),
            &info,
            DIB_RGB_COLORS,
            SRCCOPY,
        )
    };
}

pub(super) fn colorref([r, g, b]: [u8; 3]) -> u32 {
    u32::from_le_bytes([r, g, b, 0])
}
pub(super) fn dib_rgb([r, g, b]: [u8; 3]) -> u32 {
    u32::from_le_bytes([b, g, r, 0])
}

pub(super) fn blend(base: [u8; 3], light: [u8; 3], amount: f32) -> [u8; 3] {
    std::array::from_fn(|i| {
        (f32::from(base[i]) * (1.0 - amount) + f32::from(light[i]) * amount).round() as u8
    })
}

pub(super) fn capsule_distance(x: f32, y: f32) -> f32 {
    let qx = (x - WIDTH as f32 / 2.0).abs() - (BODY_WIDTH as f32 / 2.0 - 20.0);
    let qy = (y - HEIGHT as f32 / 2.0).abs() - (BODY_HEIGHT as f32 / 2.0 - 20.0);
    qx.max(0.0).hypot(qy.max(0.0)) + qx.max(qy).min(0.0) - 20.0
}

pub(super) fn capsule_color(x: f32, y: f32, glow: f32) -> [u8; 3] {
    let tone = ((y - SHADOW_PAD as f32) / BODY_HEIGHT as f32).clamp(0.0, 1.0);
    let distance = capsule_distance(x, y);
    let mut color = blend([28, 28, 30], [255; 3], (26.0 - 18.0 * tone) / 255.0);
    color = blend(color, [100, 210, 255], 0.1 * glow * tone);
    let noise =
        (x as u32).wrapping_mul(0x045d_9f3b).rotate_right(9) ^ (y as u32).wrapping_mul(0x9e37_79b9);
    let grain = if noise.count_ones() & 1 == 0 { 255 } else { 0 };
    color = blend(color, [grain; 3], 0.009);
    // The hairline sits inside a darker transition so it follows the capsule instead of reading as an outline.
    if distance >= -4.0 {
        color = blend(
            color,
            [0; 3],
            (distance + 4.0).clamp(0.0, 1.0) * (72.0 / 255.0),
        );
    }
    if distance >= -2.0 {
        let edge = if tone < 0.35 {
            1.0 - tone / 0.35 * (137.0 / 255.0)
        } else {
            (118.0 - (tone - 0.35) / 0.65 * 30.0) / 255.0
        };
        color = blend(color, [255; 3], (distance + 2.5).clamp(0.0, 1.0) * edge);
    }
    color
}

pub(super) fn capsule_shadow(x: f32, y: f32, opacity: f32) -> u8 {
    let distance = capsule_distance(x, y);
    if !(0.0..4.5).contains(&distance) {
        return 0;
    }
    let mut alpha = 0.0;
    for (expansion, shade) in [(4.0, 9.0), (3.0, 9.0), (2.0, 18.0), (1.0, 18.0)] {
        let cover = (expansion + 0.5 - distance).clamp(0.0, 1.0);
        let layer = shade / 255.0 * opacity * cover;
        alpha += (1.0 - alpha) * layer;
    }
    (alpha * 255.0).round() as u8
}

pub(super) fn draw_backdrop(
    backdrop: HWND,
    x: i32,
    y: i32,
    width: i32,
    height: i32,
    opacity: f64,
    glow: f32,
) -> io::Result<()> {
    if backdrop.is_null() || width <= 0 || height <= 0 {
        return Ok(());
    }
    let bitmap = overlay_bitmap(width as u32, height as u32, opacity as f32, glow);
    BACKDROP_SURFACE.with_borrow_mut(|cache| {
        if cache
            .as_ref()
            .is_none_or(|&(w, h, _)| (w, h) != (bitmap.width, bitmap.height))
        {
            let surface =
                LayeredSurface::new(bitmap.width, bitmap.height).map_err(io::Error::other)?;
            *cache = Some((bitmap.width, bitmap.height, surface));
        }
        cache
            .as_mut()
            .expect("backdrop surface is initialized above")
            .2
            .present(backdrop, Point::new(f64::from(x), f64::from(y)), &bitmap)
            .map_err(io::Error::other)
    })
}

thread_local! {
    static BACKDROP_SURFACE: std::cell::RefCell<Option<(u32, u32, LayeredSurface)>> =
        const { std::cell::RefCell::new(None) };
}

pub(super) fn overlay_bitmap(width: u32, height: u32, opacity: f32, glow: f32) -> WheelBitmap {
    let mut pixels = vec![0; (width * height * 4) as usize];
    let scale = width as f32 / WIDTH as f32;
    for (index, pixel) in pixels.as_chunks_mut::<4>().0.iter_mut().enumerate() {
        let x = index % width as usize;
        let y = index / width as usize;
        let px = (x as f32 + 0.5) / scale;
        let py = (y as f32 + 0.5) / scale;
        let shadow = u32::from(capsule_shadow(px, py, opacity.clamp(0.0, 1.0)));
        let inside = capsule_alpha(px, py);
        let background = (opacity.clamp(0.0, 1.0) * inside * 255.0).round() as u32;
        // Both layers are dark; premultiplied color is the capsule tint only.
        let [red, green, blue] = capsule_color(px, py, glow);
        let alpha = background + shadow * (255 - background) / 255;
        if alpha == 0 {
            continue;
        }
        pixel[0] = (u32::from(blue) * background / 255) as u8;
        pixel[1] = (u32::from(green) * background / 255) as u8;
        pixel[2] = (u32::from(red) * background / 255) as u8;
        pixel[3] = alpha as u8;
    }
    WheelBitmap {
        width,
        height,
        pixels,
    }
}

pub(super) fn capsule_alpha(x: f32, y: f32) -> f32 {
    const LEFT: f32 = 9.0;
    const TOP: f32 = 9.0;
    const RIGHT: f32 = 547.0;
    const BOTTOM: f32 = 65.0;
    const RADIUS: f32 = 20.0;
    let dx = (LEFT + RADIUS - x).max(x - (RIGHT - RADIUS)).max(0.0);
    let dy = (TOP + RADIUS - y).max(y - (BOTTOM - RADIUS)).max(0.0);
    let distance = (dx * dx + dy * dy).sqrt() - RADIUS;
    (0.5 - distance).clamp(0.0, 1.0)
}

pub(super) fn capsule_icon_color(gear: bool, hovered: bool, enabled: bool) -> [u8; 3] {
    if hovered && (gear || enabled) {
        [64, 156, 255]
    } else if gear || enabled {
        [152, 152, 157]
    } else {
        [188, 188, 188]
    }
}

pub(super) fn capsule_icon_at(x: f32, y: f32, gear: bool) -> bool {
    if !gear {
        // The play glyph's three vertices match the original 20px icon canvas.
        return (-5.0..=8.0).contains(&x) && y.abs() <= 8.0 * (8.0 - x) / 13.0;
    }
    if x.abs() > 12.0 || y.abs() > 12.0 {
        return false;
    }
    // Contours follow the checked-in Material Symbols SVG's 960-unit viewbox.
    const OUTER: &[(i16, i16)] = &[
        (370, 880),
        (354, 752),
        (330, 740),
        (307, 725),
        (188, 775),
        (78, 585),
        (181, 507),
        (180, 480),
        (181, 453),
        (78, 375),
        (188, 185),
        (307, 235),
        (330, 220),
        (354, 208),
        (370, 80),
        (590, 80),
        (606, 208),
        (631, 220),
        (653, 235),
        (772, 185),
        (882, 375),
        (779, 453),
        (780, 480),
        (778, 507),
        (881, 585),
        (771, 775),
        (653, 725),
        (630, 740),
        (606, 752),
        (590, 880),
    ];
    const INNER: &[(i16, i16)] = &[
        (440, 800),
        (519, 800),
        (533, 694),
        (590, 671),
        (639, 633),
        (738, 674),
        (777, 606),
        (691, 541),
        (698, 512),
        (700, 480),
        (698, 449),
        (691, 419),
        (777, 354),
        (738, 286),
        (639, 328),
        (591, 290),
        (533, 266),
        (520, 160),
        (441, 160),
        (427, 266),
        (370, 290),
        (321, 327),
        (222, 286),
        (183, 354),
        (269, 418),
        (262, 448),
        (260, 480),
        (262, 511),
        (269, 541),
        (183, 606),
        (222, 674),
        (321, 632),
        (370, 671),
        (427, 694),
    ];
    let x = 480.0 + x * 40.0;
    let y = 480.0 + y * 40.0;
    (x - 482.0).hypot(y - 480.0) <= 140.0 || (polygon_at(OUTER, x, y) && !polygon_at(INNER, x, y))
}

pub(super) fn polygon_at(points: &[(i16, i16)], x: f32, y: f32) -> bool {
    points.iter().zip(points.iter().cycle().skip(1)).fold(
        false,
        |inside, (&(x1, y1), &(x2, y2))| {
            let (x1, y1, x2, y2) = (f32::from(x1), f32::from(y1), f32::from(x2), f32::from(y2));
            inside ^ ((y1 > y) != (y2 > y) && x < (x2 - x1) * (y - y1) / (y2 - y1) + x1)
        },
    )
}
pub(super) fn capsule_glow(hwnd: HWND) -> f32 {
    let step = unsafe { GetPropW(hwnd, w!("WordyFocusGlow")) as usize };
    1.0 - (1.0 - step as f32 / GLOW_STEPS as f32).powi(3)
}
