use std::error::Error;
use std::fmt;

#[derive(Debug, PartialEq, Eq)]
pub enum HotkeyError {
    MissingKey(String),
    MultipleKeys(String),
    UnsupportedKey(String),
}

impl fmt::Display for HotkeyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingKey(binding) => write!(formatter, "快捷键缺少主按键: {binding}"),
            Self::MultipleKeys(binding) => {
                write!(formatter, "快捷键只能包含一个非修饰键: {binding}")
            }
            Self::UnsupportedKey(key) => write!(formatter, "不支持的快捷键按键: {key}"),
        }
    }
}

impl Error for HotkeyError {}

fn normalize_key_part(key: &str) -> String {
    key.trim().to_lowercase().replace('_', " ")
}

fn key_parts(binding: &str) -> impl Iterator<Item = (String, String)> + '_ {
    // Existing settings use keyboard's convention for names containing spaces.
    binding.split('+').filter_map(|part| {
        let raw = part.replace(' ', "_");
        let key = normalize_key_part(&raw);
        (!key.is_empty()).then_some((raw, key))
    })
}

fn modifier(key: &str) -> Option<(u32, u32)> {
    Some(match key {
        "alt" => (1, 0x12),
        "left alt" => (1, 0xa4),
        "right alt" => (1, 0xa5),
        "ctrl" | "control" => (2, 0x11),
        "left ctrl" | "left control" => (2, 0xa2),
        "right ctrl" | "right control" => (2, 0xa3),
        "shift" => (4, 0x10),
        "left shift" => (4, 0xa0),
        "right shift" => (4, 0xa1),
        "win" | "windows" | "left win" | "left windows" => (8, 0x5b),
        "right win" | "right windows" => (8, 0x5c),
        _ => return None,
    })
}

fn main_key(key: &str) -> Option<u32> {
    let key_code = match key {
        "backspace" => 0x08,
        "tab" => 0x09,
        "clear" => 0x0c,
        "enter" | "return" => 0x0d,
        "pause" => 0x13,
        "caps lock" | "capslock" => 0x14,
        "esc" | "escape" => 0x1b,
        "space" | "spacebar" => 0x20,
        "page up" => 0x21,
        "page down" => 0x22,
        "end" => 0x23,
        "home" => 0x24,
        "left" => 0x25,
        "up" => 0x26,
        "right" => 0x27,
        "down" => 0x28,
        "print screen" => 0x2c,
        "insert" | "ins" => 0x2d,
        "delete" | "del" => 0x2e,
        "menu" | "apps" => 0x5d,
        "num lock" | "numlock" => 0x90,
        "scroll lock" | "scrolllock" => 0x91,
        ";" | ":" => 0xba,
        "=" | "+" => 0xbb,
        "," | "<" => 0xbc,
        "-" => 0xbd,
        "." | ">" => 0xbe,
        "/" | "?" => 0xbf,
        "`" | "~" => 0xc0,
        "[" | "{" => 0xdb,
        "\\" | "|" => 0xdc,
        "]" | "}" => 0xdd,
        "'" | "\"" => 0xde,
        _ => {
            if let [byte] = key.as_bytes()
                && byte.is_ascii_alphanumeric()
            {
                return Some(u32::from(byte.to_ascii_uppercase()));
            }
            if let Some(index) = key.strip_prefix('f').and_then(number) {
                return (1..=24).contains(&index).then(|| 0x70 + index - 1);
            }
            let index = key.strip_prefix("num ").and_then(number)?;
            return (index <= 9).then(|| 0x60 + index);
        }
    };
    Some(key_code)
}

fn number(value: &str) -> Option<u32> {
    if value.bytes().all(|byte| byte.is_ascii_digit()) {
        value.parse().ok()
    } else {
        None
    }
}

pub fn virtual_key_for_key_name(name: &str) -> Option<u32> {
    let key = normalize_key_part(name);
    modifier(&key)
        .map(|(_, code)| code)
        .or_else(|| main_key(&key))
}

pub(crate) fn key_name(code: u32) -> Option<String> {
    let name = match code {
        0x08 => "backspace",
        0x09 => "tab",
        0x0c => "clear",
        0x0d => "enter",
        0x10 => "shift",
        0x11 => "ctrl",
        0x12 => "alt",
        0x13 => "pause",
        0x14 => "caps lock",
        0x1b => "esc",
        0x20 => "space",
        0x21 => "page up",
        0x22 => "page down",
        0x23 => "end",
        0x24 => "home",
        0x25 => "left",
        0x26 => "up",
        0x27 => "right",
        0x28 => "down",
        0x2c => "print screen",
        0x2d => "insert",
        0x2e => "delete",
        0x5b => "left windows",
        0x5c => "right windows",
        0x5d => "menu",
        0x90 => "num lock",
        0x91 => "scroll lock",
        0xa0 => "left shift",
        0xa1 => "right shift",
        0xa2 => "left ctrl",
        0xa3 => "right ctrl",
        0xa4 => "left alt",
        0xa5 => "right alt",
        0xba => ";",
        0xbb => "=",
        0xbc => ",",
        0xbd => "-",
        0xbe => ".",
        0xbf => "/",
        0xc0 => "`",
        0xdb => "[",
        0xdc => "\\",
        0xdd => "]",
        0xde => "'",
        0x30..=0x39 | 0x41..=0x5a => {
            return Some(char::from(code as u8).to_ascii_lowercase().to_string());
        }
        0x60..=0x69 => return Some(format!("num {}", code - 0x60)),
        0x70..=0x87 => return Some(format!("f{}", code - 0x70 + 1)),
        _ => return None,
    };
    Some(name.into())
}

pub(crate) fn is_modifier(name: &str) -> bool {
    modifier(name).is_some()
}

pub fn parse_hotkey(binding: &str) -> Result<(u32, u32), HotkeyError> {
    let mut modifiers = 0x4000; // MOD_NOREPEAT prevents repeated activation while held.
    let mut key_code = None;
    for (raw, key) in key_parts(binding) {
        if let Some((flag, _)) = modifier(&key) {
            modifiers |= flag;
        } else if key_code.is_some() {
            return Err(HotkeyError::MultipleKeys(binding.to_owned()));
        } else {
            key_code = Some(main_key(&key).ok_or(HotkeyError::UnsupportedKey(raw))?);
        }
    }
    key_code
        .map(|code| (modifiers, code))
        .ok_or_else(|| HotkeyError::MissingKey(binding.to_owned()))
}

pub fn display_hotkey(binding: &str) -> String {
    let parts: Vec<_> = key_parts(binding)
        .map(|(_, key)| match key.as_str() {
            "ctrl" | "control" => "Ctrl".to_owned(),
            "win" | "windows" | "left windows" | "right windows" => "Win".to_owned(),
            _ if key.chars().count() == 1 => key.to_uppercase(),
            _ => key
                .split_whitespace()
                .map(|word| {
                    let mut letters = word.chars();
                    let first = letters
                        .next()
                        .expect("split_whitespace yields nonempty words");
                    format!("{}{}", first.to_uppercase(), letters.as_str())
                })
                .collect::<Vec<_>>()
                .join(" "),
        })
        .collect();
    if parts.is_empty() {
        binding.to_owned()
    } else {
        parts.join("+")
    }
}

#[cfg(windows)]
pub mod windows {
    use super::virtual_key_for_key_name;
    use std::{io, marker::PhantomData, mem, ptr, rc::Rc, thread, time::Duration};
    use windows_sys::Win32::Foundation::ERROR_HOTKEY_ALREADY_REGISTERED;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        GetAsyncKeyState, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY,
        KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, MAPVK_VK_TO_VSC_EX, MapVirtualKeyW, RegisterHotKey,
        SendInput, UnregisterHotKey,
    };

    pub struct Registration {
        id: i32,
        // Thread-level registrations can only be removed by their creating message pump.
        thread: PhantomData<Rc<()>>,
    }

    impl Registration {
        pub fn register(id: i32, (modifiers, key): (u32, u32)) -> io::Result<Self> {
            // A null window delivers WM_HOTKEY to the calling thread's message queue.
            if unsafe { RegisterHotKey(ptr::null_mut(), id, modifiers, key) } == 0 {
                let error = io::Error::last_os_error();
                if error.raw_os_error() == Some(ERROR_HOTKEY_ALREADY_REGISTERED as i32) {
                    return Err(io::Error::new(
                        io::ErrorKind::AlreadyExists,
                        "快捷键已被其他程序占用",
                    ));
                }
                return Err(error);
            }
            Ok(Self {
                id,
                thread: PhantomData,
            })
        }
    }

    impl Drop for Registration {
        fn drop(&mut self) {
            // The registration cannot leave its owning message thread.
            if unsafe { UnregisterHotKey(ptr::null_mut(), self.id) } == 0 {
                eprintln!(
                    "注销 Windows 原生全局快捷键失败: {}",
                    io::Error::last_os_error()
                );
            }
        }
    }

    pub fn is_down(key: u16) -> bool {
        // Sign is the current pressed state; the low bit is shared with other processes.
        unsafe { GetAsyncKeyState(i32::from(key)) < 0 }
    }

    pub struct PressedKey {
        key: u16,
        scan: u32,
    }

    impl PressedKey {
        pub fn press(name: &str) -> io::Result<Self> {
            let key = virtual_key_for_key_name(name)
                .and_then(|key| u16::try_from(key).ok())
                .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "不支持的语音按键"))?;
            // Resolve once so a layout change during playback cannot alter the released key.
            let scan = unsafe { MapVirtualKeyW(u32::from(key), MAPVK_VK_TO_VSC_EX) };
            Self::send(key, scan, 0)?;
            // Give the foreground game time to open its voice input before writing PCM.
            thread::sleep(Duration::from_millis(50));
            Ok(Self { key, scan })
        }

        fn send(key: u16, scan: u32, event_flags: u32) -> io::Result<()> {
            let extended = scan >> 8 == 0xe0;
            let use_scan = scan != 0 && scan >> 8 != 0xe1;
            let flags = event_flags
                | if extended { KEYEVENTF_EXTENDEDKEY } else { 0 }
                | if use_scan { KEYEVENTF_SCANCODE } else { 0 };
            let event = INPUT {
                r#type: INPUT_KEYBOARD,
                Anonymous: INPUT_0 {
                    ki: KEYBDINPUT {
                        wVk: if use_scan { 0 } else { key },
                        wScan: (scan & 0xff) as u16,
                        dwFlags: flags,
                        time: 0,
                        dwExtraInfo: 0,
                    },
                },
            };
            // SendInput reads one initialized keyboard input of the platform ABI size.
            if unsafe { SendInput(1, &event, mem::size_of::<INPUT>() as i32) } == 1 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        }
    }

    impl Drop for PressedKey {
        fn drop(&mut self) {
            if let Err(error) = Self::send(self.key, self.scan, KEYEVENTF_KEYUP) {
                eprintln!("自动松开语音按键失败: {error}");
            }
        }
    }
}
