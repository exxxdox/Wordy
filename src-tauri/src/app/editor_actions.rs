use super::*;
use crate::config::{WHEEL_NAME_MAX_LENGTH, Wheel};

const SLOT_LABEL_LIMIT: usize = 40;
const SLOT_TEXT_LIMIT: usize = 200;

impl App {
    fn wheel_at(&self, index: usize) -> io::Result<Wheel> {
        self.config
            .settings()
            .wheel(index)
            .ok_or_else(|| io::Error::other("轮盘不存在"))
    }

    /// 槽位是否已有音频或文字；标签不算内容，与默认落点用同一判定。
    pub(super) fn slot_has_content(&self, index: usize, slot: usize) -> bool {
        self.config
            .settings()
            .wheel(index)
            .filter(|_| slot < crate::config::SLOT_COUNT)
            .is_some_and(|wheel| !wheel.slot_is_empty(slot))
    }

    /// 导入或保存录音只替换音频并切到音频模式，文字仍保留。
    pub(super) fn confirm_slot_replace(&mut self, index: usize, slot: usize) -> bool {
        !self.slot_has_content(index, slot)
            || self.confirm_dialog("替换槽位", "该槽已有内容，继续会覆盖旧音频。是否继续？")
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
        self.wheel_at(index)?;
        next.discard_wheel(index);
        next.chat_wheel_last_index = index.min(next.wheel_count() - 1);
        self.hotkeys.update(&mut self.config, next)?;
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
        audio::set_slot_label(&mut self.config, index, slot, &label)?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn slot_set_text(
        &mut self,
        index: usize,
        slot: usize,
        value: &str,
    ) -> io::Result<()> {
        self.wheel_at(index)?;
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
        // 只切播放模式，音频与文字都保留，切回时恢复原来的内容和设置。
        self.wheel_at(index)?;
        self.stop_editor_preview();
        self.config
            .update_wheel(index, |wheel| wheel.text_modes[slot] = to_text)?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn slot_clear(&mut self, index: usize, slot: usize) -> io::Result<()> {
        // 清空槽位会移除文字与标签，解除音频引用后仍可在整理页找回。
        if self.slot_has_content(index, slot)
            && !self.confirm_dialog("清空槽位", "清空该槽的文字、标签和音频关联？")
        {
            return Ok(());
        }
        self.stop_editor_preview();
        self.config.update_wheel(index, |wheel| {
            wheel.slots[slot].clear();
            wheel.labels[slot].clear();
            wheel.texts[slot].clear();
            wheel.text_modes[slot] = false;
        })?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn slot_import(&mut self, index: usize, slot: usize) -> io::Result<()> {
        let filter = "音频文件\0*.wav;*.mp3;*.ogg;*.flac;*.m4a;*.aac;*.wma;*.aiff;*.aif;*.opus\0";
        let directory = std::env::current_dir()?;
        let Some(path) = dialog::file(self.host, "导入音频", filter, &directory, None, None, false)
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
        if item.slot_is_empty(slot) {
            return Ok(());
        }
        let text = item.text_mode(slot);
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
}
