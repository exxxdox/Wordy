use super::*;
use crate::config::{CHAT_WHEEL_STYLES, SLOT_COUNT, WHEEL_NAME_MAX_LENGTH, Wheel};
use crate::wheel::WheelStyle;
use crate::wheel_paint::{WheelFrame, WheelRenderer};

const SLOT_LABEL_LIMIT: usize = 40;
const SLOT_TEXT_LIMIT: usize = 200;

impl App {
    fn wheel_at(&self, index: usize) -> io::Result<Wheel> {
        self.config
            .settings()
            .wheel(index)
            .ok_or_else(|| io::Error::other("轮盘不存在"))
    }

    pub(super) fn wheel_select(&mut self, index: usize) -> io::Result<()> {
        self.wheel_at(index)?;
        self.config
            .update(|settings| settings.chat_wheel_last_index = index)?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn wheel_add(&mut self) -> io::Result<()> {
        append_unbound_wheel(&mut self.config, &mut self.hotkeys)?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn wheel_remove(&mut self, index: usize) -> io::Result<()> {
        let mut next = self.config.settings().clone();
        if next.wheel_count() == 1 {
            return Err(io::Error::other("至少保留一个轮盘"));
        }
        if !self.confirm_dialog("删除轮盘", "删除此轮盘及其槽位？") {
            return Ok(());
        }
        let removed = next
            .wheel(index)
            .ok_or_else(|| io::Error::other("轮盘不存在"))?;
        next.discard_wheel(index);
        next.chat_wheel_last_index = index.min(next.wheel_count() - 1);
        self.hotkeys.update(&mut self.config, next)?;
        for path in removed.slots.iter().filter(|path| !path.is_empty()) {
            let _ = audio::remove_unreferenced_clip(&self.config, path);
        }
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn wheel_set_name(&mut self, index: usize, value: &str) -> io::Result<()> {
        let name: String = value.trim().chars().take(WHEEL_NAME_MAX_LENGTH).collect();
        self.config
            .update_wheel(index, |wheel| wheel.name.clone_from(&name))?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn wheel_set_style(&mut self, style: &str) -> io::Result<()> {
        if !CHAT_WHEEL_STYLES.contains(&style) {
            return Err(io::Error::other("未知轮盘样式"));
        }
        self.config
            .update(|settings| settings.chat_wheel_style = style.into())?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn wheel_record_hotkey(&mut self, index: usize) -> io::Result<()> {
        let Some(hotkey) = self.capture_binding(false)? else {
            return Ok(());
        };
        let mut next = self.config.settings().clone();
        let mut wheel = next
            .wheel(index)
            .ok_or_else(|| io::Error::other("轮盘不存在"))?;
        wheel.hotkey = hotkey;
        next.assign_wheel(index, wheel);
        self.hotkeys.update(&mut self.config, next)?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn slot_set_label(
        &mut self,
        index: usize,
        slot: usize,
        value: &str,
    ) -> io::Result<()> {
        let label: String = value.trim().chars().take(SLOT_LABEL_LIMIT).collect();
        self.config
            .update_wheel(index, |wheel| wheel.labels[slot].clone_from(&label))?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn slot_set_text(
        &mut self,
        index: usize,
        slot: usize,
        value: &str,
    ) -> io::Result<()> {
        let current = self.wheel_at(index)?;
        // 音频槽与文字互斥；音频槽的文字输入不生效。
        if !current.slots[slot].is_empty() {
            return Ok(());
        }
        let text: String = value.trim().chars().take(SLOT_TEXT_LIMIT).collect();
        self.config
            .update_wheel(index, |wheel| wheel.texts[slot].clone_from(&text))?;
        self.ui_dirty = true;
        Ok(())
    }

    /// 网页滑块传百分比（0–300），配置保存为 0.0–3.0 的增益。
    pub(super) fn slot_set_volume(
        &mut self,
        index: usize,
        slot: usize,
        percent: f64,
    ) -> io::Result<()> {
        if !percent.is_finite() {
            return Err(io::Error::other("请输入有效音量"));
        }
        let gain = percent.clamp(0.0, 300.0) / 100.0;
        self.config.update_wheel(index, |wheel| {
            if !wheel.slots[slot].is_empty() {
                wheel.volumes[slot] = gain;
            }
        })?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn slot_set_mode(
        &mut self,
        index: usize,
        slot: usize,
        to_text: bool,
    ) -> io::Result<()> {
        let current = self.wheel_at(index)?;
        if to_text
            && !current.slots[slot].is_empty()
            && !self.confirm_dialog("切换为文字", "切换为文字会删除该槽音频，继续？")
        {
            return Ok(());
        }
        self.stop_editor_preview();
        if (to_text && !current.slots[slot].is_empty())
            || (!to_text && !current.texts[slot].is_empty())
        {
            self.config.update_wheel(index, |wheel| {
                wheel.slots[slot].clear();
                wheel.texts[slot].clear();
            })?;
            if !current.slots[slot].is_empty() {
                let _ = audio::remove_unreferenced_clip(&self.config, &current.slots[slot]);
            }
            self.ui_dirty = true;
        }
        Ok(())
    }

    pub(super) fn slot_clear(&mut self, index: usize, slot: usize) -> io::Result<()> {
        self.stop_editor_preview();
        let old = self.wheel_at(index)?.slots[slot].clone();
        self.config.update_wheel(index, |wheel| {
            wheel.slots[slot].clear();
            wheel.labels[slot].clear();
            wheel.texts[slot].clear();
        })?;
        if !old.is_empty() {
            let _ = audio::remove_unreferenced_clip(&self.config, &old);
        }
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn slot_import(&mut self, index: usize, slot: usize) -> io::Result<()> {
        let filter = "音频文件\0*.wav;*.mp3;*.ogg;*.flac;*.m4a;*.aac;*.wma;*.aiff;*.aif;*.opus\0";
        let directory = std::env::current_dir()?;
        let Some(path) = dialog::file(self.host, "导入音频", filter, &directory, None, false)
        else {
            return Ok(());
        };
        self.import_slot_clip(index, slot, path)
    }

    pub(super) fn slot_crop(&mut self, index: usize, slot: usize) -> io::Result<()> {
        let item = self.wheel_at(index)?;
        if item.slots[slot].is_empty() {
            return Ok(());
        }
        let path = audio::resolve_clip(self.config.path(), &item.slots[slot])?;
        self.prepare_trim(index, slot, path)
    }

    pub(super) fn slot_preview(&mut self, index: usize, slot: usize) -> io::Result<()> {
        if self.editor_preview.is_some() {
            self.stop_editor_preview();
            return Ok(());
        }
        if self.trim.is_some() {
            return Ok(());
        }
        let item = self.wheel_at(index)?;
        if item.texts[slot].is_empty() && item.slots[slot].is_empty() {
            return Ok(());
        }
        let text = !item.texts[slot].is_empty();
        let id = self.next_job + 1;
        self.editor_preview = Some(EditorPreview {
            id,
            text,
            wheel: index,
            slot,
        });
        if let Err(error) = self.play_slot(index, slot) {
            self.editor_preview = None;
            return Err(error);
        }
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn stop_editor_preview(&mut self) {
        if let Some(preview) = self.editor_preview.take() {
            if preview.text {
                self.playback.stop_speech();
            } else {
                self.playback.stop_clip();
            }
            self.ui_dirty = true;
        }
    }

    /// 网页预览：用与运行时轮盘相同的绘制器生成 PNG（data URI）。
    pub(super) fn render_wheel_preview(
        &mut self,
        index: usize,
        slot: usize,
        width: u32,
    ) -> Result<String, String> {
        use base64::Engine;
        let settings = self.config.settings();
        let wheel = settings
            .wheel(index)
            .ok_or_else(|| "轮盘不存在".to_string())?;
        let labels = crate::wheel::labels(&wheel);
        let mut frame =
            WheelFrame::new(&labels, WheelStyle::from_config(&settings.chat_wheel_style));
        frame.font = crate::config::ui_font_family(&settings.ui_font);
        frame.editing = true;
        let slot = slot.min(SLOT_COUNT - 1);
        frame.selected = Some(slot);
        let mut highlights = [0.0; SLOT_COUNT];
        highlights[slot] = 1.0;
        frame.highlights = Some(highlights);
        if self.wheel_renderer.is_none() {
            self.wheel_renderer = Some(WheelRenderer::new().map_err(|error| error.to_string())?);
        }
        let renderer = self.wheel_renderer.as_ref().expect("renderer initialized");
        let width = f64::from(width.clamp(200, 1200));
        let bitmap = renderer
            .render(frame, width / crate::wheel::CANVAS_WIDTH)
            .map_err(|error| error.to_string())?;
        let mut rgba = Vec::with_capacity(bitmap.pixels.len());
        for pixel in bitmap.pixels.as_chunks::<4>().0 {
            let [b, g, r, a] = *pixel;
            if a == 0 {
                rgba.extend_from_slice(&[0, 0, 0, 0]);
            } else {
                let scale = 255.0 / f32::from(a);
                rgba.extend_from_slice(&[
                    (f32::from(r) * scale).min(255.0) as u8,
                    (f32::from(g) * scale).min(255.0) as u8,
                    (f32::from(b) * scale).min(255.0) as u8,
                    a,
                ]);
            }
        }
        let mut encoded = Vec::new();
        let mut encoder = png::Encoder::new(&mut encoded, bitmap.width, bitmap.height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().map_err(|error| error.to_string())?;
        writer
            .write_image_data(&rgba)
            .map_err(|error| error.to_string())?;
        writer.finish().map_err(|error| error.to_string())?;
        Ok(format!(
            "data:image/png;base64,{}",
            base64::engine::general_purpose::STANDARD.encode(encoded)
        ))
    }
}
