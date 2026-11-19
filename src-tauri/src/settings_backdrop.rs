//! 深色对话框共用的圆角与填充绘制。

use windows_sys::Win32::{Foundation::RECT, Graphics::Gdi::*};

pub(crate) fn scaled(units: i32, dpi: i32) -> i32 {
    (units * dpi.max(96) + 48) / 96
}

pub(crate) fn fill(dc: HDC, rect: &RECT, color: u32) {
    unsafe {
        SetDCBrushColor(dc, color);
        FillRect(dc, rect, GetStockObject(DC_BRUSH));
    }
}

pub(crate) fn round(dc: HDC, rect: &RECT, radius: i32, border: u32, background: Option<u32>) {
    let diameter = radius * 2;
    unsafe {
        let pen = SelectObject(dc, GetStockObject(DC_PEN));
        let brush = SelectObject(
            dc,
            GetStockObject(if background.is_some() {
                DC_BRUSH
            } else {
                NULL_BRUSH
            }),
        );
        SetDCPenColor(dc, border);
        if let Some(color) = background {
            SetDCBrushColor(dc, color);
        }
        RoundRect(
            dc,
            rect.left,
            rect.top,
            rect.right,
            rect.bottom,
            diameter,
            diameter,
        );
        SelectObject(dc, brush);
        SelectObject(dc, pen);
    }
}
