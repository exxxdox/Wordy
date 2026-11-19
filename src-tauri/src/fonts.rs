use std::ptr;

use windows_sys::Win32::Foundation::LPARAM;
use windows_sys::Win32::Graphics::Gdi::{
    DEFAULT_CHARSET, EnumFontFamiliesExW, GetDC, LOGFONTW, RASTER_FONTTYPE, ReleaseDC, TEXTMETRICW,
};

const DEFAULT_FAMILY: &str = crate::config::DEFAULT_UI_FONT;

/// 设置页的字体下拉框需要真实的系统字体列表；网页界面同样使用这份数据。
pub fn installed_fonts() -> Vec<String> {
    let mut family_names: Vec<String> = Vec::new();
    let query = LOGFONTW {
        lfCharSet: DEFAULT_CHARSET,
        ..Default::default()
    };
    let dc = unsafe { GetDC(ptr::null_mut()) };
    if !dc.is_null() {
        unsafe {
            EnumFontFamiliesExW(
                dc,
                &query,
                Some(add_font),
                ptr::from_mut(&mut family_names) as LPARAM,
                0,
            );
            ReleaseDC(ptr::null_mut(), dc);
        }
    }
    if !family_names.iter().any(|name| name == DEFAULT_FAMILY) {
        family_names.push(DEFAULT_FAMILY.into());
    }
    family_names.sort_by_cached_key(|name| name.to_lowercase());
    family_names.dedup();
    family_names
}

unsafe extern "system" fn add_font(
    font: *const LOGFONTW,
    _: *const TEXTMETRICW,
    font_type: u32,
    data: LPARAM,
) -> i32 {
    if font_type & RASTER_FONTTYPE == 0 {
        let units = &unsafe { &*font }.lfFaceName;
        let length = units
            .iter()
            .position(|&unit| unit == 0)
            .unwrap_or(units.len());
        let name = String::from_utf16_lossy(&units[..length]);
        if !name.is_empty() && !name.starts_with('@') {
            unsafe { &mut *(data as *mut Vec<String>) }.push(name);
        }
    }
    1
}

/// 未安装的字体回退到内置字体栈。
pub fn usable_font<'a>(families: &[String], configured: &'a str) -> &'a str {
    if families.iter().any(|family| family == configured) {
        configured
    } else {
        DEFAULT_FAMILY
    }
}
