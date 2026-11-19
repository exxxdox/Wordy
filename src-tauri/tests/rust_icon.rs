// 回归测试：发布版 exe 必须在 PE 资源中带图标，否则资源管理器只显示默认图标。
// 运行时 WM_SETICON 使用 include_bytes! 的图标，不影响 exe 文件图标。

use std::fs;

fn u16_at(data: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([data[offset], data[offset + 1]])
}

fn u32_at(data: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
    ])
}

/// 读取 PE 资源目录第一层的资源类型 ID。
fn resource_type_ids(data: &[u8]) -> Option<Vec<u32>> {
    let pe = u32_at(data, 0x3c) as usize;
    let coff = pe + 4;
    let section_count = u16_at(data, coff + 2) as usize;
    let optional_size = u16_at(data, coff + 16) as usize;
    let optional = coff + 20;
    let data_directories = match u16_at(data, optional) {
        0x20b => optional + 112,
        0x10b => optional + 96,
        _ => return None,
    };
    let resource_rva = u32_at(data, data_directories + 2 * 8) as usize;
    if resource_rva == 0 {
        return None;
    }

    let sections = optional + optional_size;
    let map_rva = |rva: usize| -> Option<usize> {
        (0..section_count).find_map(|index| {
            let section = sections + index * 40;
            let virtual_size = u32_at(data, section + 8) as usize;
            let virtual_address = u32_at(data, section + 12) as usize;
            let raw_offset = u32_at(data, section + 20) as usize;
            let inside = rva >= virtual_address && rva < virtual_address + virtual_size;
            inside.then(|| raw_offset + rva - virtual_address)
        })
    };

    let base = map_rva(resource_rva)?;
    // 目录头：NumberOfNamedEntries @12，NumberOfIdEntries @14。
    let named = u16_at(data, base + 12) as usize;
    let count = named + u16_at(data, base + 14) as usize;
    Some(
        (0..count)
            .map(|index| u32_at(data, base + 16 + index * 8) & 0x7fff_ffff)
            .collect(),
    )
}

#[test]
fn built_binary_embeds_icon_resources() {
    let exe = env!("CARGO_BIN_EXE_wordy");
    let data = fs::read(exe).expect("read built executable");
    let types = resource_type_ids(&data).expect("missing PE resource directory");

    assert!(
        types.contains(&3),
        "missing RT_ICON resource, types: {types:?}"
    );
    assert!(
        types.contains(&14),
        "missing RT_GROUP_ICON resource, types: {types:?}"
    );
}
