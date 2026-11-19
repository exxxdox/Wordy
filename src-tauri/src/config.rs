use std::{
    collections::HashSet,
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use toml::{Table, Value};

use crate::{
    credentials::{CredentialStore, Provider, WindowsCredentials},
    hotkey::{display_hotkey, parse_hotkey, virtual_key_for_key_name},
};

pub const SLOT_COUNT: usize = 8;
pub const WHEEL_NAME_MAX_LENGTH: usize = 20;
pub const SAVE_DEBOUNCE: Duration = Duration::from_millis(150);
const SAVE_RETRY: Duration = Duration::from_secs(1);
pub const LOG_LEVELS: [&str; 5] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
pub const DEFAULT_UI_FONT: &str = "Segoe UI Variable";

pub fn ui_font_family(configured: &str) -> &str {
    if configured.is_empty() {
        DEFAULT_UI_FONT
    } else {
        configured
    }
}
pub const CARTESIA_BYTES: &str = "Cartesia Bytes";
pub const CARTESIA_REALTIME: &str = "Cartesia Realtime";
pub const VOLCENGINE_STREAMING: &str = "Volcengine Streaming";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AudioIdentity {
    pub name: String,
    pub host_api_name: Option<String>,
    // Windows endpoint id keeps two identically named devices apart.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct WindowPosition {
    pub x: i32,
    pub y: i32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Voice {
    pub id: String,
    pub name: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct CartesiaSettings {
    pub voice_id: Option<String>,
    pub voice_name: Option<String>,
    pub backend: String,
    pub api_key_set: bool,
    pub api_key_storage: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct VolcengineSettings {
    pub voice_list: Vec<Voice>,
    pub backend: String,
    pub api_key_set: bool,
    pub api_key_storage: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ProviderSettings {
    #[serde(rename = "Cartesia")]
    pub cartesia: CartesiaSettings,
    #[serde(rename = "Volcengine")]
    pub volcengine: VolcengineSettings,
}

impl Default for ProviderSettings {
    fn default() -> Self {
        Self {
            cartesia: CartesiaSettings {
                voice_id: None,
                voice_name: None,
                backend: CARTESIA_BYTES.into(),
                api_key_set: false,
                api_key_storage: "none".into(),
            },
            volcengine: VolcengineSettings {
                voice_list: Vec::new(),
                backend: VOLCENGINE_STREAMING.into(),
                api_key_set: false,
                api_key_storage: "none".into(),
            },
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Wheel {
    pub name: String,
    pub hotkey: String,
    pub slots: [String; SLOT_COUNT],
    pub labels: [String; SLOT_COUNT],
    pub texts: [String; SLOT_COUNT],
    pub volumes: [f64; SLOT_COUNT],
}

impl Wheel {
    pub fn new(hotkey: impl Into<String>) -> Self {
        Self {
            name: String::new(),
            hotkey: hotkey.into(),
            slots: Default::default(),
            labels: Default::default(),
            texts: Default::default(),
            volumes: [1.0; SLOT_COUNT],
        }
    }

    fn from_table(table: &Table) -> Option<Self> {
        // An unparseable hotkey must not delete the wheel and its slots. Keep it
        // unbound, keep its data, and let the wheel editor record a new key.
        let raw_hotkey = text(table, "hotkey").map(str::trim).unwrap_or_default();
        let hotkey = if parse_hotkey(raw_hotkey).is_ok() {
            raw_hotkey.to_owned()
        } else {
            String::new()
        };
        let wheel = Self {
            name: text(table, "name").map(wheel_name).unwrap_or_default(),
            hotkey,
            slots: read_slot_texts(table, "slots", true),
            labels: read_slot_texts(table, "labels", false),
            texts: read_slot_texts(table, "texts", false),
            volumes: read_slot_volumes(table, "volumes"),
        };
        let has_data = !wheel.name.is_empty()
            || !wheel.hotkey.is_empty()
            || wheel.slots.iter().any(|slot| !slot.is_empty())
            || wheel.labels.iter().any(|label| !label.is_empty())
            || wheel.texts.iter().any(|text| !text.is_empty());
        has_data.then_some(wheel)
    }

    fn normalize(&mut self) {
        self.name = wheel_name(&self.name);
        for slot in &mut self.slots {
            if !is_clip_name(slot) {
                slot.clear();
            }
        }
        for volume in &mut self.volumes {
            *volume = finite_clamp(*volume, 0.0, 3.0).unwrap_or(1.0);
        }
    }
}

// The first wheel remains flat on disk, so Python and Rust can share the
// existing config and backups through an upgrade or rollback.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Settings {
    pub hotkey: String,
    pub name: String,
    pub chat_wheel_name: String,
    pub chat_wheel_hotkey: String,
    pub chat_wheel_slots: [String; SLOT_COUNT],
    pub chat_wheel_labels: [String; SLOT_COUNT],
    pub chat_wheel_texts: [String; SLOT_COUNT],
    pub chat_wheel_volumes: [f64; SLOT_COUNT],
    pub additional_chat_wheels: Vec<Wheel>,
    pub chat_wheel_last_index: usize,
    pub rolling_buffer_hotkey: String,
    pub rolling_buffer_seconds: f64,
    pub hold_key_enabled: bool,
    pub hold_key: String,
    pub volume: f64,
    pub overlay_opacity: f64,
    pub overlay_placeholder: String,
    pub ui_font: String,
    pub fixed_center: bool,
    pub log_level: String,
    pub window_position: Option<WindowPosition>,
    /// 设置窗口上次所在位置；未设置时首次出现在屏幕中间。
    pub settings_window_position: Option<WindowPosition>,
    pub main_playback_enabled: bool,
    pub audio_output_device_name: Option<String>,
    pub audio_output_device: Option<AudioIdentity>,
    pub tts_providers: ProviderSettings,
    pub active_tts_provider: Provider,
    pub audio_routing_enabled: bool,
    pub mic_input_device: Option<String>,
    pub virtual_output_device: Option<String>,
    pub sidetone_enabled: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            hotkey: "f6".into(),
            name: "F6".into(),
            chat_wheel_name: "轮盘 1".into(),
            chat_wheel_hotkey: "f8".into(),
            chat_wheel_slots: Default::default(),
            chat_wheel_labels: Default::default(),
            chat_wheel_texts: Default::default(),
            chat_wheel_volumes: [1.0; SLOT_COUNT],
            additional_chat_wheels: Vec::new(),
            chat_wheel_last_index: 0,
            rolling_buffer_hotkey: "f10".into(),
            rolling_buffer_seconds: 30.0,
            hold_key_enabled: false,
            hold_key: "g".into(),
            volume: 1.0,
            overlay_opacity: 1.0,
            overlay_placeholder: "Hello World~".into(),
            ui_font: String::new(),
            fixed_center: true,
            log_level: "INFO".into(),
            window_position: None,
            settings_window_position: None,
            main_playback_enabled: true,
            audio_output_device_name: None,
            audio_output_device: None,
            tts_providers: ProviderSettings::default(),
            active_tts_provider: Provider::Volcengine,
            audio_routing_enabled: false,
            mic_input_device: None,
            virtual_output_device: None,
            sidetone_enabled: false,
        }
    }
}

impl Settings {
    pub fn from_toml(source: &str) -> Result<Self, toml::de::Error> {
        let raw = toml::from_str::<Table>(source)?;
        Ok(Self::from_table(&raw))
    }

    pub fn from_table(raw: &Table) -> Self {
        let defaults = Self::default();
        let hotkey = nonempty_text(raw, "hotkey");
        let additional_chat_wheels: Vec<Wheel> = raw
            .get("additional_chat_wheels")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_table)
            .filter_map(Wheel::from_table)
            .collect();
        let last_wheel = additional_chat_wheels.len();
        let mut settings = Self {
            name: hotkey
                .map(|key| {
                    nonempty_text(raw, "name").map_or_else(|| display_hotkey(key), str::to_owned)
                })
                .unwrap_or(defaults.name),
            hotkey: hotkey.map(str::to_owned).unwrap_or(defaults.hotkey),
            chat_wheel_name: text(raw, "chat_wheel_name")
                .map(wheel_name)
                .unwrap_or_default(),
            chat_wheel_hotkey: valid_hotkey(raw, "chat_wheel_hotkey")
                .unwrap_or(defaults.chat_wheel_hotkey),
            chat_wheel_slots: read_slot_texts(raw, "chat_wheel_slots", true),
            chat_wheel_labels: read_slot_texts(raw, "chat_wheel_labels", false),
            chat_wheel_texts: read_slot_texts(raw, "chat_wheel_texts", false),
            chat_wheel_volumes: read_slot_volumes(raw, "chat_wheel_volumes"),
            additional_chat_wheels,
            chat_wheel_last_index: raw
                .get("chat_wheel_last_index")
                .and_then(Value::as_integer)
                .map_or(0, |index| clamp_index(index, last_wheel)),
            rolling_buffer_hotkey: valid_hotkey(raw, "rolling_buffer_hotkey")
                .unwrap_or(defaults.rolling_buffer_hotkey),
            rolling_buffer_seconds: read_float(
                raw,
                "rolling_buffer_seconds",
                5.0,
                300.0,
                defaults.rolling_buffer_seconds,
            ),
            hold_key_enabled: read_bool(raw, "hold_key_enabled", defaults.hold_key_enabled),
            hold_key: string_or(raw, "hold_key", defaults.hold_key),
            volume: read_float(raw, "volume", 0.5, 2.0, defaults.volume),
            overlay_opacity: read_float(
                raw,
                "overlay_opacity",
                0.30,
                1.0,
                defaults.overlay_opacity,
            ),
            overlay_placeholder: string_or(
                raw,
                "overlay_placeholder",
                defaults.overlay_placeholder,
            ),
            ui_font: text(raw, "ui_font")
                .map(str::trim)
                .unwrap_or_default()
                .into(),
            fixed_center: read_bool(raw, "fixed_center", defaults.fixed_center),
            log_level: text(raw, "log_level")
                .map(|level| level.trim().to_ascii_uppercase())
                .unwrap_or(defaults.log_level),
            window_position: raw
                .get("window_position")
                .and_then(Value::as_table)
                .and_then(read_position),
            settings_window_position: raw
                .get("settings_window_position")
                .and_then(Value::as_table)
                .and_then(read_position),
            main_playback_enabled: read_bool(
                raw,
                "main_playback_enabled",
                defaults.main_playback_enabled,
            ),
            audio_output_device_name: nonempty_text(raw, "audio_output_device_name")
                .map(str::to_owned),
            audio_output_device: raw
                .get("audio_output_device")
                .and_then(Value::as_table)
                .and_then(read_identity),
            tts_providers: raw
                .get("tts_providers")
                .and_then(Value::as_table)
                .map(read_providers)
                .unwrap_or_default(),
            active_tts_provider: match text(raw, "active_tts_provider") {
                Some("Cartesia") => Provider::Cartesia,
                _ => Provider::Volcengine,
            },
            audio_routing_enabled: read_bool(
                raw,
                "audio_routing_enabled",
                defaults.audio_routing_enabled,
            ),
            mic_input_device: nonempty_text(raw, "mic_input_device").map(str::to_owned),
            virtual_output_device: nonempty_text(raw, "virtual_output_device").map(str::to_owned),
            sidetone_enabled: read_bool(raw, "sidetone_enabled", defaults.sidetone_enabled),
        };
        settings.normalize();
        settings
    }

    pub fn to_toml(&self) -> Result<String, toml::ser::Error> {
        toml::to_string_pretty(self)
    }

    pub fn wheel_count(&self) -> usize {
        1 + self.additional_chat_wheels.len()
    }

    pub fn wheel(&self, index: usize) -> Option<Wheel> {
        if index == 0 {
            Some(Wheel {
                name: self.chat_wheel_name.clone(),
                hotkey: self.chat_wheel_hotkey.clone(),
                slots: self.chat_wheel_slots.clone(),
                labels: self.chat_wheel_labels.clone(),
                texts: self.chat_wheel_texts.clone(),
                volumes: self.chat_wheel_volumes,
            })
        } else {
            self.additional_chat_wheels.get(index - 1).cloned()
        }
    }

    /// 后台录制保存的默认落点：按轮盘顺序找第一个空槽，全满时回到第一盘槽 1。
    pub fn empty_slot_target(&self) -> (usize, usize) {
        (0..self.wheel_count())
            .find_map(|index| {
                let wheel = self.wheel(index)?;
                (0..SLOT_COUNT)
                    .find(|&slot| wheel.slots[slot].is_empty() && wheel.texts[slot].is_empty())
                    .map(|slot| (index, slot))
            })
            .unwrap_or((0, 0))
    }

    pub fn referenced_audio(&self) -> impl Iterator<Item = &str> {
        self.chat_wheel_slots
            .iter()
            .chain(
                self.additional_chat_wheels
                    .iter()
                    .flat_map(|wheel| &wheel.slots),
            )
            .filter(|name| !name.is_empty())
            .map(String::as_str)
    }

    pub fn active_voice_id(&self) -> Option<&str> {
        match self.active_tts_provider {
            Provider::Cartesia => self.tts_providers.cartesia.voice_id.as_deref(),
            Provider::Volcengine => self
                .tts_providers
                .volcengine
                .voice_list
                .first()
                .map(|voice| voice.id.as_str()),
        }
    }

    pub fn active_backend(&self) -> &str {
        match self.active_tts_provider {
            Provider::Cartesia => &self.tts_providers.cartesia.backend,
            Provider::Volcengine => &self.tts_providers.volcengine.backend,
        }
    }

    pub fn set_credential_status(&mut self, provider: Provider, present: bool) {
        let (flag, storage) = match provider {
            Provider::Cartesia => (
                &mut self.tts_providers.cartesia.api_key_set,
                &mut self.tts_providers.cartesia.api_key_storage,
            ),
            Provider::Volcengine => (
                &mut self.tts_providers.volcengine.api_key_set,
                &mut self.tts_providers.volcengine.api_key_storage,
            ),
        };
        *flag = present;
        *storage = if present { "keyring" } else { "none" }.into();
    }

    pub(crate) fn assign_wheel(&mut self, index: usize, wheel: Wheel) {
        if index == 0 {
            self.chat_wheel_name = wheel.name;
            self.chat_wheel_hotkey = wheel.hotkey;
            self.chat_wheel_slots = wheel.slots;
            self.chat_wheel_labels = wheel.labels;
            self.chat_wheel_texts = wheel.texts;
            self.chat_wheel_volumes = wheel.volumes;
        } else {
            self.additional_chat_wheels[index - 1] = wheel;
        }
    }

    pub(crate) fn discard_wheel(&mut self, index: usize) {
        if index == 0 {
            let successor = self.additional_chat_wheels.remove(0);
            self.assign_wheel(0, successor);
        } else {
            self.additional_chat_wheels.remove(index - 1);
        }
    }

    fn normalize(&mut self) {
        if !LOG_LEVELS.contains(&self.log_level.as_str()) {
            self.log_level = "INFO".into();
        }
        self.hold_key = if virtual_key_for_key_name(&self.hold_key).is_some() {
            self.hold_key.trim().to_lowercase()
        } else {
            "g".into()
        };
        let last_wheel = self.additional_chat_wheels.len();
        self.chat_wheel_last_index = self.chat_wheel_last_index.min(last_wheel);
        let mut first = self.wheel(0).expect("the first wheel always exists");
        first.normalize();
        // 未命名的轮盘统一显示默认名，设置页与选择器保持一致。
        if first.name.is_empty() {
            first.name = "轮盘 1".into();
        }
        self.assign_wheel(0, first);
        for (index, wheel) in self.additional_chat_wheels.iter_mut().enumerate() {
            wheel.normalize();
            if wheel.name.is_empty() {
                wheel.name = format!("轮盘 {}", index + 2);
            }
        }
        if ![CARTESIA_BYTES, CARTESIA_REALTIME]
            .contains(&self.tts_providers.cartesia.backend.as_str())
        {
            self.tts_providers.cartesia.backend = CARTESIA_BYTES.into();
        }
        if self.tts_providers.volcengine.backend != VOLCENGINE_STREAMING {
            self.tts_providers.volcengine.backend = VOLCENGINE_STREAMING.into();
        }
        let mut seen = HashSet::new();
        self.tts_providers
            .volcengine
            .voice_list
            .retain_mut(|voice| {
                voice.id = voice.id.trim().into();
                voice.name = voice.name.trim().into();
                if voice.name.is_empty() {
                    voice.name.clone_from(&voice.id);
                }
                !voice.id.is_empty() && seen.insert(voice.id.clone())
            });
    }

    fn prepare_write(&mut self) -> io::Result<()> {
        self.volume =
            finite_clamp(self.volume, 0.5, 2.0).ok_or_else(|| invalid("配置数值必须是有限值"))?;
        self.overlay_opacity = finite_clamp(self.overlay_opacity, 0.30, 1.0)
            .ok_or_else(|| invalid("配置数值必须是有限值"))?;
        self.rolling_buffer_seconds = finite_clamp(self.rolling_buffer_seconds, 5.0, 300.0)
            .ok_or_else(|| invalid("配置数值必须是有限值"))?;
        self.normalize();
        Ok(())
    }
}

pub fn config_directory() -> PathBuf {
    std::env::var("XDG_CONFIG_HOME")
        .ok()
        .map(|value| PathBuf::from(value.trim()))
        .filter(|path| path.is_absolute())
        .unwrap_or_else(|| std::env::home_dir().unwrap_or_default().join(".config"))
        .join("wordy")
}

#[derive(Debug)]
pub struct ConfigStore {
    path: PathBuf,
    settings: Settings,
    save_deadline: Option<Instant>,
    load_warning: Option<String>,
    /// 每次配置内容变化递增，网页据此判断是否需要刷新快照。
    revision: u64,
}

impl ConfigStore {
    pub fn load(path: impl Into<PathBuf>) -> io::Result<Self> {
        let path = path.into();
        let mut load_warning = None;
        let settings = match fs::read_to_string(&path) {
            Ok(contents) => match Settings::from_toml(&contents) {
                Ok(settings) => settings,
                Err(error) => {
                    // A syntax error must not make the whole app unusable. Keep the
                    // original bytes for repair and start from defaults.
                    let mut broken = path.with_extension("toml.broken");
                    let mut index = 1;
                    while broken.exists() {
                        broken = path.with_extension(format!("toml.broken.{index}"));
                        index += 1;
                    }
                    fs::rename(&path, &broken).map_err(|rename| {
                        invalid(format!(
                            "无法读取配置文件：{}；且无法备份损坏文件：{rename}",
                            error.message()
                        ))
                    })?;
                    load_warning = Some(format!(
                        "配置文件损坏，已备份到 {}，本次使用默认设置",
                        broken.display()
                    ));
                    Settings::default()
                }
            },
            Err(error) if error.kind() == io::ErrorKind::NotFound => Settings::default(),
            Err(error) => return Err(error),
        };
        Ok(Self {
            path,
            settings,
            save_deadline: None,
            load_warning,
            revision: 0,
        })
    }

    pub fn take_load_warning(&mut self) -> Option<String> {
        self.load_warning.take()
    }

    pub fn load_default() -> io::Result<Self> {
        let mut config = Self::load(config_directory().join("config.toml"))?;
        config.refresh_credentials(&WindowsCredentials);
        Ok(config)
    }

    pub fn revision(&self) -> u64 {
        self.revision
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn settings(&self) -> &Settings {
        &self.settings
    }

    pub fn refresh_credentials(&mut self, credentials: &dyn CredentialStore) {
        for provider in [Provider::Cartesia, Provider::Volcengine] {
            self.settings.set_credential_status(
                provider,
                credentials.load(provider).is_ok_and(|key| key.is_some()),
            );
        }
    }

    pub fn update(&mut self, edit: impl FnOnce(&mut Settings)) -> io::Result<()> {
        let mut next = self.settings.clone();
        edit(&mut next);
        self.replace(next)
    }

    pub fn replace(&mut self, mut next: Settings) -> io::Result<()> {
        next.prepare_write()?;
        // Publish the new snapshot after the original file has been replaced.
        write_atomic(&self.path, &next)?;
        self.settings = next;
        self.save_deadline = None;
        self.revision += 1;
        Ok(())
    }

    pub fn update_debounced(&mut self, edit: impl FnOnce(&mut Settings)) -> io::Result<()> {
        let mut next = self.settings.clone();
        edit(&mut next);
        next.prepare_write()?;
        self.settings = next;
        self.save_deadline = Some(Instant::now() + SAVE_DEBOUNCE);
        self.revision += 1;
        Ok(())
    }

    pub fn save_deadline(&self) -> Option<Instant> {
        self.save_deadline
    }

    /// 记录设置窗口位置。拖动会连续触发移动事件，用防抖写盘；
    /// 位置不参与网页快照，因此不递增 revision。
    pub fn remember_settings_position(&mut self, position: WindowPosition) {
        if self.settings.settings_window_position == Some(position) {
            return;
        }
        self.settings.settings_window_position = Some(position);
        self.save_deadline = Some(Instant::now() + SAVE_DEBOUNCE);
    }

    pub fn save_if_due(&mut self, now: Instant) -> io::Result<bool> {
        if self.save_deadline.is_some_and(|deadline| now >= deadline) {
            self.flush()?;
            return Ok(true);
        }
        Ok(false)
    }

    pub fn flush(&mut self) -> io::Result<()> {
        match write_atomic(&self.path, &self.settings) {
            Ok(()) => {
                self.save_deadline = None;
                Ok(())
            }
            Err(error) => {
                // Keep retrying while the app runs; a transient disk error must
                // not leave the newest settings unsaved until the next edit.
                self.save_deadline = Some(Instant::now() + SAVE_RETRY);
                Err(error)
            }
        }
    }

    pub fn update_wheel(&mut self, index: usize, edit: impl FnOnce(&mut Wheel)) -> io::Result<()> {
        let mut wheel = self
            .settings
            .wheel(index)
            .ok_or_else(|| invalid("轮盘不存在"))?;
        edit(&mut wheel);
        // Empty means unbound after a bad legacy hotkey; only check real values.
        if !wheel.hotkey.trim().is_empty() {
            parse_hotkey(&wheel.hotkey).map_err(|_| invalid("轮盘快捷键无效"))?;
        }
        self.update(|settings| settings.assign_wheel(index, wheel))
    }
}

fn write_atomic(path: &Path, settings: &Settings) -> io::Result<()> {
    let parent = path
        .parent()
        .filter(|dir| !dir.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let serialized = settings
        .to_toml()
        .map_err(|error| invalid(error.to_string()))?;
    fs::create_dir_all(parent)?;
    let mut staging = tempfile::Builder::new()
        .prefix(".atomic_wordy_")
        .suffix(".toml")
        .tempfile_in(parent)?;
    staging.write_all(serialized.as_bytes())?;
    staging.flush()?;
    staging.as_file().sync_all()?;
    staging.persist(path).map_err(|error| error.error)?;
    Ok(())
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

fn text<'a>(table: &'a Table, key: &str) -> Option<&'a str> {
    table.get(key)?.as_str()
}

fn nonempty_text<'a>(table: &'a Table, key: &str) -> Option<&'a str> {
    text(table, key)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn string_or(table: &Table, key: &str, default: String) -> String {
    text(table, key).map(str::to_owned).unwrap_or(default)
}

fn valid_hotkey(table: &Table, key: &str) -> Option<String> {
    text(table, key)
        .filter(|key| parse_hotkey(key).is_ok())
        .map(str::to_owned)
}

fn wheel_name(value: &str) -> String {
    value.trim().chars().take(WHEEL_NAME_MAX_LENGTH).collect()
}

fn is_clip_name(value: &str) -> bool {
    !value.contains(['/', '\\', ':']) && !matches!(value, "." | "..")
}

fn read_slot_texts(table: &Table, key: &str, filenames: bool) -> [String; SLOT_COUNT] {
    let values = table.get(key).and_then(Value::as_array);
    std::array::from_fn(|index| {
        values
            .and_then(|entries| entries.get(index))
            .and_then(Value::as_str)
            .filter(|value| !filenames || is_clip_name(value))
            .unwrap_or_default()
            .into()
    })
}

fn read_slot_volumes(table: &Table, key: &str) -> [f64; SLOT_COUNT] {
    let values = table.get(key).and_then(Value::as_array);
    std::array::from_fn(|index| {
        values
            .and_then(|entries| entries.get(index))
            .and_then(number)
            .and_then(|value| finite_clamp(value, 0.0, 3.0))
            .unwrap_or(1.0)
    })
}

fn finite_clamp(value: f64, min: f64, max: f64) -> Option<f64> {
    value.is_finite().then(|| value.clamp(min, max))
}

fn number(value: &Value) -> Option<f64> {
    value
        .as_float()
        .or_else(|| value.as_integer().map(|integer| integer as f64))
}

fn read_float(table: &Table, key: &str, min: f64, max: f64, default: f64) -> f64 {
    table
        .get(key)
        .and_then(number)
        .and_then(|value| finite_clamp(value, min, max))
        .unwrap_or(default)
}

fn read_bool(table: &Table, key: &str, default: bool) -> bool {
    table.get(key).and_then(Value::as_bool).unwrap_or(default)
}

fn clamp_index(index: i64, max: usize) -> usize {
    index.clamp(0, max as i64) as usize
}

fn read_position(table: &Table) -> Option<WindowPosition> {
    let x = i32::try_from(table.get("x")?.as_integer()?).ok()?;
    let y = i32::try_from(table.get("y")?.as_integer()?).ok()?;
    Some(WindowPosition { x, y })
}

fn read_identity(table: &Table) -> Option<AudioIdentity> {
    let name = nonempty_text(table, "name")?.into();
    let optional = |key: &str| match table.get(key) {
        Some(Value::String(value)) => Some(value.trim())
            .filter(|value| !value.is_empty())
            .map(str::to_owned),
        Some(_) => None,
        None => None,
    };
    if table
        .get("host_api_name")
        .is_some_and(|value| !matches!(value, Value::String(_)))
        || table
            .get("id")
            .is_some_and(|value| !matches!(value, Value::String(_)))
    {
        return None;
    }
    Some(AudioIdentity {
        name,
        host_api_name: optional("host_api_name"),
        id: optional("id"),
    })
}

fn read_providers(table: &Table) -> ProviderSettings {
    let mut providers = ProviderSettings::default();
    if let Some(cartesia) = table.get("Cartesia").and_then(Value::as_table) {
        providers.cartesia.voice_id = text(cartesia, "voice_id").map(str::to_owned);
        providers.cartesia.voice_name = text(cartesia, "voice_name").map(str::to_owned);
        if let Some(backend) = text(cartesia, "backend") {
            providers.cartesia.backend = backend.into();
        }
        providers.cartesia.api_key_set = read_bool(cartesia, "api_key_set", false);
        providers.cartesia.api_key_storage = string_or(
            cartesia,
            "api_key_storage",
            providers.cartesia.api_key_storage,
        );
    }
    if let Some(volcengine) = table.get("Volcengine").and_then(Value::as_table) {
        providers.volcengine.voice_list = volcengine
            .get("voice_list")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_table)
            .filter_map(|entry| {
                Some(Voice {
                    id: text(entry, "id")?.into(),
                    name: text(entry, "name").unwrap_or_default().into(),
                })
            })
            .collect();
        providers.volcengine.api_key_set = read_bool(volcengine, "api_key_set", false);
        providers.volcengine.api_key_storage = string_or(
            volcengine,
            "api_key_storage",
            providers.volcengine.api_key_storage,
        );
    }
    providers
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn reads_existing_toml_with_field_local_fallbacks() {
        let settings = Settings::from_toml(
            r#"
            hotkey = "ctrl+f6"
            ui_font = "  中文  "
            overlay_placeholder = ""
            volume = inf
            overlay_opacity = 0
            chat_wheel_slots = ["a.wav", "../x.wav", 17]
            chat_wheel_labels = ["你好"]
            chat_wheel_volumes = [-1, 5, nan]
            additional_chat_wheels = [
              { hotkey = "ctrl+f9", name = "第二个" },
              { hotkey = "bogus" },
            ]
            chat_wheel_last_index = 99
            hold_key = " LEFT CTRL "
            audio_output_device_name = "  speakers  "
            main_playback_enabled = false
            [tts_providers.Volcengine]
            voice_id = "legacy"
            voice_list = [{id = " a "}, {id = "a", name = "dup"}, {id = "b", name = "语音"}]
            [tts_providers.Cartesia]
            backend = "Cartesia Realtime"
            voice_id = "cart"
            "#,
        )
        .unwrap();
        assert_eq!(settings.name, "Ctrl+F6");
        assert_eq!(settings.ui_font, "中文");
        assert!(settings.overlay_placeholder.is_empty());
        assert_eq!(settings.volume, 1.0);
        assert_eq!(settings.overlay_opacity, 0.30);
        assert_eq!(&settings.chat_wheel_slots[..3], ["a.wav", "", ""]);
        assert_eq!(&settings.chat_wheel_volumes[..4], [0.0, 3.0, 1.0, 1.0]);
        assert_eq!(settings.wheel_count(), 2);
        assert_eq!(settings.wheel(1).unwrap().name, "第二个");
        assert_eq!(settings.chat_wheel_last_index, 1);
        assert_eq!(settings.hold_key, "left ctrl");
        assert_eq!(
            settings.audio_output_device_name.as_deref(),
            Some("speakers")
        );
        assert_eq!(settings.tts_providers.cartesia.backend, "Cartesia Realtime");
        assert_eq!(settings.tts_providers.volcengine.voice_list[0].name, "a");
        assert_eq!(settings.tts_providers.volcengine.voice_list.len(), 2);
        assert!(!settings.main_playback_enabled);
        assert_eq!(
            Settings::from_toml(&settings.to_toml().unwrap()).unwrap(),
            settings
        );
    }

    #[test]
    fn defaults_serialize_with_first_wheel_and_two_providers() {
        let settings = Settings::default();
        let serialized = settings.to_toml().unwrap();
        let table = toml::from_str::<Table>(&serialized).unwrap();
        assert_eq!(table["chat_wheel_slots"].as_array().unwrap().len(), 8);
        assert_eq!(
            table["tts_providers"]["Cartesia"]["backend"].as_str(),
            Some("Cartesia Bytes")
        );
        assert!(table["tts_providers"]["Cartesia"].get("voice_id").is_none());
        assert!(
            table["tts_providers"]["Volcengine"]
                .get("voice_id")
                .is_none()
        );
        assert_eq!(settings, Settings::from_toml(&serialized).unwrap());
    }

    #[test]
    fn invalid_config_is_preserved_and_the_app_starts_with_defaults() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        fs::write(&path, "text = [").unwrap();
        let mut config = ConfigStore::load(&path).unwrap();
        assert_eq!(config.settings(), &Settings::default());
        assert!(config.take_load_warning().is_some());
        assert!(!path.exists());
        assert_eq!(
            fs::read_to_string(dir.path().join("config.toml.broken")).unwrap(),
            "text = ["
        );
    }

    #[test]
    fn atomic_update_rolls_back_and_removes_staged_file_if_replacement_fails() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut config = ConfigStore::load(&path).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(config.update(|settings| settings.volume = 1.5).is_err());
        assert_eq!(config.settings().volume, 1.0);
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
    }

    #[test]
    fn moving_wheels_preserves_all_slots_and_reclamps_selection() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut config = ConfigStore::load(&path).unwrap();
        config
            .update(|settings| {
                settings.additional_chat_wheels.push(Wheel::new("ctrl+f9"));
            })
            .unwrap();
        config
            .update_wheel(1, |wheel| {
                wheel.slots[7] = "x.wav".into();
                wheel.labels[7] = "名称".into();
                wheel.volumes[7] = 2.5;
            })
            .unwrap();
        config.update(|settings| settings.discard_wheel(0)).unwrap();
        let loaded = ConfigStore::load(&path).unwrap();
        assert_eq!(loaded.settings().wheel_count(), 1);
        let wheel = loaded.settings().wheel(0).unwrap();
        assert_eq!(wheel.hotkey, "ctrl+f9");
        assert_eq!(wheel.slots[7], "x.wav");
        assert_eq!(wheel.labels[7], "名称");
        assert_eq!(wheel.volumes[7], 2.5);
    }

    #[test]
    fn wheel_added_without_a_hotkey_survives_a_reload() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut config = ConfigStore::load(&path).unwrap();
        config
            .update(|settings| {
                let mut wheel = Wheel::new("");
                wheel.name = format!("轮盘 {}", settings.wheel_count() + 1);
                settings.additional_chat_wheels.push(wheel);
                settings.chat_wheel_last_index = settings.wheel_count() - 1;
            })
            .unwrap();
        let loaded = ConfigStore::load(&path).unwrap();
        assert_eq!(loaded.settings().wheel_count(), 2);
        let wheel = loaded.settings().wheel(1).unwrap();
        assert!(wheel.hotkey.is_empty());
        assert_eq!(wheel.name, "轮盘 2");
    }

    #[test]
    fn empty_ui_font_uses_the_built_in_family() {
        assert_eq!(ui_font_family(""), DEFAULT_UI_FONT);
        assert_eq!(ui_font_family("Consolas"), "Consolas");
    }

    #[test]
    fn unnamed_wheels_get_numbered_default_names() {
        let settings = Settings::from_toml("").unwrap();
        assert_eq!(settings.chat_wheel_name, "轮盘 1");
        let settings = Settings::from_toml(
            r#"
            additional_chat_wheels = [
                { hotkey = "", slots = [""], labels = ["第一个"], texts = [""], volumes = [1.0] },
                { name = "自定义", hotkey = "", slots = [""], labels = [""], texts = [""], volumes = [1.0] },
            ]
            "#,
        )
        .unwrap();
        assert_eq!(settings.additional_chat_wheels[0].name, "轮盘 2");
        assert_eq!(settings.additional_chat_wheels[1].name, "自定义");
    }

    #[test]
    fn deferred_update_is_replaced_and_flushed_once() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut config = ConfigStore::load(&path).unwrap();
        config.update_debounced(|s| s.volume = 1.5).unwrap();
        config.update_debounced(|s| s.volume = 1.8).unwrap();
        assert_eq!(config.settings().volume, 1.8);
        let deadline = config.save_deadline().unwrap();
        assert!(
            !config
                .save_if_due(deadline - Duration::from_millis(1))
                .unwrap()
        );
        assert!(!path.exists());
        assert!(config.save_if_due(deadline).unwrap());
        assert!(!config.save_if_due(deadline).unwrap());
        assert_eq!(ConfigStore::load(&path).unwrap().settings().volume, 1.8);
    }

    #[test]
    fn bad_wheel_hotkey_keeps_the_wheel_and_allows_edits() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        fs::write(
            &path,
            r#"
            additional_chat_wheels = [{ hotkey = "media play", slots = ["a.wav"], labels = ["lbl"], texts = [""], volumes = [1.0] }]
            "#,
        )
        .unwrap();
        let mut config = ConfigStore::load(&path).unwrap();
        let wheel = config.settings().wheel(1).unwrap();
        assert!(wheel.hotkey.is_empty());
        assert_eq!(wheel.slots[0], "a.wav");
        assert_eq!(wheel.labels[0], "lbl");
        config
            .update_wheel(1, |wheel| wheel.labels[0] = "new".into())
            .unwrap();
        let loaded = ConfigStore::load(&path).unwrap();
        let wheel = loaded.settings().wheel(1).unwrap();
        assert_eq!(wheel.slots[0], "a.wav");
        assert_eq!(wheel.labels[0], "new");
    }

    #[test]
    fn failed_flush_keeps_a_retry_deadline() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut config = ConfigStore::load(&path).unwrap();
        config.update_debounced(|s| s.volume = 1.5).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(config.flush().is_err());
        assert!(config.save_deadline().is_some());
    }

    #[test]
    fn remembering_the_settings_window_position_defers_the_write() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut config = ConfigStore::load(&path).unwrap();
        let revision = config.revision();
        config.remember_settings_position(WindowPosition { x: 240, y: 160 });
        // 位置不参与网页快照，revision 不变；写盘走防抖。
        assert_eq!(config.revision(), revision);
        assert!(config.save_deadline().is_some());
        config.flush().unwrap();
        let loaded = ConfigStore::load(&path).unwrap();
        assert_eq!(
            loaded.settings().settings_window_position,
            Some(WindowPosition { x: 240, y: 160 })
        );
    }

    #[test]
    fn empty_slot_target_prefers_the_first_empty_slot() {
        let mut settings = Settings::default();
        settings.chat_wheel_slots[0] = "a.wav".into();
        settings.chat_wheel_texts[1] = "你好".into();
        assert_eq!(settings.empty_slot_target(), (0, 2));

        // 第一盘满了才轮到后续轮盘。
        settings.chat_wheel_slots = std::array::from_fn(|_| "a.wav".into());
        settings.chat_wheel_texts = Default::default();
        settings.additional_chat_wheels.push(Wheel::new("ctrl+f9"));
        assert_eq!(settings.empty_slot_target(), (1, 0));
    }

    #[test]
    fn empty_slot_target_falls_back_to_the_first_slot_when_every_wheel_is_full() {
        let mut settings = Settings::default();
        settings.chat_wheel_slots = std::array::from_fn(|_| "a.wav".into());
        settings.chat_wheel_texts = std::array::from_fn(|_| "文字".into());
        assert_eq!(settings.empty_slot_target(), (0, 0));
    }

    #[test]
    fn endpoint_id_survives_a_config_round_trip() {
        let settings = Settings::from_toml(
            "[audio_output_device]\nname = 'Speakers'\nhost_api_name = 'WASAPI'\nid = 'endpoint-id'\n",
        )
        .unwrap();
        assert_eq!(
            settings.audio_output_device.as_ref().unwrap().id.as_deref(),
            Some("endpoint-id")
        );
        let loaded = Settings::from_toml(&settings.to_toml().unwrap()).unwrap();
        assert_eq!(
            loaded.audio_output_device.as_ref().unwrap().id.as_deref(),
            Some("endpoint-id")
        );
    }
}
