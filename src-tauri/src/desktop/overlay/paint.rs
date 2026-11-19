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
    // Plain sqrt: the coordinates are small, and hypot's overflow guards cost
    // more than they save in this per-pixel, subpixel-sampled loop.
    let (cx, cy) = (qx.max(0.0), qy.max(0.0));
    (cx * cx + cy * cy).sqrt() + qx.max(qy).min(0.0) - 20.0
}

pub(super) fn capsule_color(x: f32, y: f32, glow: f32) -> [u8; 3] {
    capsule_color_at(x, y, capsule_distance(x, y), glow)
}

/// Distance first: the overlay renders every pixel through the alpha, shadow
/// and color functions, and recomputing the signed distance per function
/// dominated the open time.
pub(super) fn capsule_color_at(x: f32, y: f32, distance: f32, glow: f32) -> [u8; 3] {
    let (dim, delta) = capsule_shade(x, y, distance);
    std::array::from_fn(|channel| {
        (f32::from(dim[channel]) + glow * delta[channel])
            .round()
            .clamp(0.0, 255.0) as u8
    })
}

/// The glow-free color and the color added by one full unit of glow. Every
/// later stage scales the earlier color uniformly, so the focus tint stays
/// linear and the backdrop cache can store these two terms instead of
/// re-rendering the silhouette for each fade step.
fn capsule_shade(x: f32, y: f32, distance: f32) -> ([u8; 3], [f32; 3]) {
    const GRAIN: f32 = 0.009;
    let tone = ((y - SHADOW_PAD as f32) / BODY_HEIGHT as f32).clamp(0.0, 1.0);
    let base = blend([28, 28, 30], [255; 3], (26.0 - 18.0 * tone) / 255.0);
    let noise =
        (x as u32).wrapping_mul(0x045d_9f3b).rotate_right(9) ^ (y as u32).wrapping_mul(0x9e37_79b9);
    let grain = if noise.count_ones() & 1 == 0 { 255 } else { 0 };
    let mut color = blend(base, [grain; 3], GRAIN);
    let mut passed = 1.0 - GRAIN;
    // A wide, shallow inner shade keeps the bright edge readable. A narrow dark
    // hairline looked like black teeth where it stair-stepped around the arcs.
    if distance >= -6.0 {
        let amount = (distance + 6.0).clamp(0.0, 1.0) * (22.0 / 255.0);
        color = blend(color, [0; 3], amount);
        passed *= 1.0 - amount;
    }
    if distance >= -2.0 {
        let edge = if tone < 0.35 {
            1.0 - tone / 0.35 * (137.0 / 255.0)
        } else {
            (118.0 - (tone - 0.35) / 0.65 * 30.0) / 255.0
        };
        let amount = (distance + 2.5).clamp(0.0, 1.0) * edge;
        color = blend(color, [255; 3], amount);
        passed *= 1.0 - amount;
    }
    const GLOW: [f32; 3] = [100.0, 210.0, 255.0];
    (
        color,
        std::array::from_fn(|channel| {
            (GLOW[channel] - f32::from(base[channel])) * 0.1 * tone * passed
        }),
    )
}

#[cfg(test)]
pub(super) fn capsule_shadow(x: f32, y: f32, opacity: f32) -> u8 {
    capsule_shadow_at(capsule_distance(x, y), opacity)
}

pub(super) fn capsule_shadow_at(distance: f32, opacity: f32) -> u8 {
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
    let (width, height, opacity) = (width as u32, height as u32, opacity as f32);
    let bitmap = overlay_bitmap(width, height, opacity, glow);
    BACKDROP_SURFACE.with_borrow_mut(|surface| {
        if surface
            .as_ref()
            .is_none_or(|&(w, h, _)| (w, h) != (bitmap.width, bitmap.height))
        {
            let layered =
                LayeredSurface::new(bitmap.width, bitmap.height).map_err(io::Error::other)?;
            *surface = Some((bitmap.width, bitmap.height, layered));
        }
        surface
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
    static BACKDROP_LAYERS: std::cell::RefCell<Option<BackdropLayers>> =
        const { std::cell::RefCell::new(None) };
}

/// Rendered backdrop kept across frames, keyed by size and opacity. The focus
/// glow only tints the capsule, and the tint is linear, so the expensive
/// supersampled pass stores the glow-free frame plus its per-glow delta; the
/// 160ms focus fade then costs one multiply per channel instead of a redraw.
struct BackdropLayers {
    width: u32,
    height: u32,
    opacity: u32,
    alpha: Vec<u8>,
    /// Premultiplied RGB, red first, at glow 0.
    base: Vec<f32>,
    /// RGB added per unit of glow.
    glow: Vec<f32>,
}

impl BackdropLayers {
    fn compose(&self, glow: f32) -> WheelBitmap {
        let mut pixels = vec![0; self.alpha.len() * 4];
        for (index, pixel) in pixels.as_chunks_mut::<4>().0.iter_mut().enumerate() {
            for channel in 0..3 {
                let value = self.base[index * 3 + channel] + glow * self.glow[index * 3 + channel];
                // The DIB keeps premultiplied BGRA.
                pixel[2 - channel] = value.round().clamp(0.0, 255.0) as u8;
            }
            pixel[3] = self.alpha[index];
        }
        WheelBitmap {
            width: self.width,
            height: self.height,
            pixels,
        }
    }
}

pub(super) fn overlay_bitmap(width: u32, height: u32, opacity: f32, glow: f32) -> WheelBitmap {
    let opacity = opacity.clamp(0.0, 1.0);
    BACKDROP_LAYERS.with_borrow_mut(|cache| {
        let stale = cache.as_ref().is_none_or(|layers| {
            (layers.width, layers.height, layers.opacity) != (width, height, opacity.to_bits())
        });
        if stale {
            *cache = Some(build_layers(width, height, opacity));
        }
        cache
            .as_ref()
            .expect("layers are cached above")
            .compose(glow)
    })
}

fn build_layers(width: u32, height: u32, opacity: f32) -> BackdropLayers {
    // The hairline is barely a pixel wide. Point sampling makes its arc
    // alternate between bright and dark pixels, which reads as black jaggies,
    // so pixels on the silhouette average a 4x4 grid of subpixel samples.
    const SAMPLES: i32 = 4;
    let count = (width * height) as usize;
    let mut alpha = vec![0; count];
    let mut base = vec![0.0; count * 3];
    let mut glow = vec![0.0; count * 3];
    let scale = width as f32 / WIDTH as f32;
    for index in 0..count {
        let x = index % width as usize;
        let y = index / width as usize;
        let px = (x as f32 + 0.5) / scale;
        let py = (y as f32 + 0.5) / scale;
        let distance = capsule_distance(px, py);
        let (alpha_value, base_value, glow_value) = if (-4.0..2.5).contains(&distance) {
            let mut alpha_sum = 0u32;
            let mut base_sum = [0.0; 3];
            let mut glow_sum = [0.0; 3];
            for row in 0..SAMPLES {
                for column in 0..SAMPLES {
                    let offset_x = (column as f32 + 0.5) / SAMPLES as f32 - 0.5;
                    let offset_y = (row as f32 + 0.5) / SAMPLES as f32 - 0.5;
                    let sample =
                        backdrop_sample(px + offset_x / scale, py + offset_y / scale, opacity);
                    alpha_sum += u32::from(sample.alpha);
                    for channel in 0..3 {
                        base_sum[channel] += sample.base[channel];
                        glow_sum[channel] += sample.glow[channel];
                    }
                }
            }
            let samples = (SAMPLES * SAMPLES) as f32;
            (
                (alpha_sum / (SAMPLES * SAMPLES) as u32) as u8,
                base_sum.map(|total| total / samples),
                glow_sum.map(|total| total / samples),
            )
        } else {
            let sample = backdrop_sample(px, py, opacity);
            (sample.alpha, sample.base, sample.glow)
        };
        alpha[index] = alpha_value;
        base[index * 3..index * 3 + 3].copy_from_slice(&base_value);
        glow[index * 3..index * 3 + 3].copy_from_slice(&glow_value);
    }
    BackdropLayers {
        width,
        height,
        opacity: opacity.to_bits(),
        alpha,
        base,
        glow,
    }
}

struct BackdropSample {
    alpha: u8,
    base: [f32; 3],
    glow: [f32; 3],
}

/// One premultiplied sample with its glow-free color and the color added at
/// full glow, before the final BGRA rounding.
fn backdrop_sample(px: f32, py: f32, opacity: f32) -> BackdropSample {
    // Shadow and color share the silhouette distance; the alpha shape is a
    // 1px-inset variant, so it still measures its own.
    let distance = capsule_distance(px, py);
    let shadow = u32::from(capsule_shadow_at(distance, opacity));
    let inside = capsule_alpha(px, py);
    let background = (opacity * inside * 255.0).round() as u32;
    let alpha = background + shadow * (255 - background) / 255;
    let (dim, delta) = capsule_shade(px, py, distance);
    let scale = background as f32 / 255.0;
    BackdropSample {
        alpha: alpha as u8,
        base: dim.map(|channel| f32::from(channel) * scale),
        glow: delta.map(|channel| channel * scale),
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
    capsule_alpha_at((dx * dx + dy * dy).sqrt() - RADIUS)
}

/// A one-pixel silhouette ramp leaves high-contrast teeth on the arcs; a
/// two-pixel ramp reads as a smooth edge instead.
pub(super) fn capsule_alpha_at(distance: f32) -> f32 {
    (0.5 - distance * 0.5).clamp(0.0, 1.0)
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
