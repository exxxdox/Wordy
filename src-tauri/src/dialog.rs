use std::mem;
use std::os::windows::ffi::OsStringExt;
use std::path::{Path, PathBuf};
use std::ptr;

use windows_sys::Win32::Foundation::HWND;
use windows_sys::Win32::UI::Controls::Dialogs::*;

use crate::desktop::wide;

/// 系统文件选择框；其余弹窗由 React 渲染。
pub fn file(
    owner: HWND,
    title: &str,
    filter: &str,
    directory: &Path,
    extension: Option<&str>,
    save: bool,
) -> Option<PathBuf> {
    let title = wide(title);
    let filter = wide(filter);
    let directory = wide(&directory.to_string_lossy());
    let extension = extension.map(wide);
    let mut name = vec![0; 32_768];
    let mut info = OPENFILENAMEW {
        lStructSize: mem::size_of::<OPENFILENAMEW>() as u32,
        hwndOwner: owner,
        lpstrFilter: filter.as_ptr(),
        nFilterIndex: 1,
        lpstrFile: name.as_mut_ptr(),
        nMaxFile: name.len() as u32,
        lpstrInitialDir: directory.as_ptr(),
        lpstrTitle: title.as_ptr(),
        Flags: OFN_EXPLORER
            | OFN_NOCHANGEDIR
            | OFN_PATHMUSTEXIST
            | if save {
                OFN_OVERWRITEPROMPT
            } else {
                OFN_FILEMUSTEXIST
            },
        lpstrDefExt: extension.as_ref().map_or(ptr::null(), |text| text.as_ptr()),
        ..Default::default()
    };
    let accepted = unsafe {
        if save {
            GetSaveFileNameW(&mut info)
        } else {
            GetOpenFileNameW(&mut info)
        }
    } != 0;
    let end = name
        .iter()
        .position(|&unit| unit == 0)
        .unwrap_or(name.len());
    accepted.then(|| std::ffi::OsString::from_wide(&name[..end]).into())
}
