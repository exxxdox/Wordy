use std::collections::HashMap;

use super::*;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(super) enum Action {
    Overlay,
    Rolling,
    Wheel(usize),
}

type Binding = (u32, u32);

pub(super) struct Registered {
    pub(super) action: Action,
    id: i32,
    _registration: Registration,
}

#[derive(Default)]
pub(super) struct Hotkeys {
    pub(super) current: HashMap<Binding, Registered>,
    next_id: i32,
}

impl Hotkeys {
    pub(super) fn register_new(
        &mut self,
        binding: Binding,
        action: Action,
    ) -> io::Result<Registered> {
        // Register replacements before dropping old bindings so failed switches preserve working keys.
        self.next_id = self.next_id % 0xbfff + 1;
        while self.current.values().any(|entry| entry.id == self.next_id) {
            self.next_id = self.next_id % 0xbfff + 1;
        }
        let id = self.next_id;
        let registration = Registration::register(id, binding)?;
        Ok(Registered {
            action,
            id,
            _registration: registration,
        })
    }

    pub(super) fn action(&self, id: usize) -> Option<(Action, u16)> {
        self.current.iter().find_map(|(&(_, key), entry)| {
            (entry.id as usize == id).then_some((entry.action, key as u16))
        })
    }

    pub(super) fn startup(&mut self, settings: &Settings) -> Vec<String> {
        let mut failures = Vec::new();
        for (action, value) in configured_keys(settings) {
            let result = (|| {
                let key = parse_hotkey(value).map_err(io::Error::other)?;
                if self.current.contains_key(&key) {
                    return Err(io::Error::other("快捷键互相冲突"));
                }
                let registration = self.register_new(key, action)?;
                self.current.insert(key, registration);
                Ok(())
            })();
            if let Err(error) = result {
                failures.push(format!("快捷键 {value} 未启用：{error}"));
            }
        }
        // Unbound wheels are a supported state (new wheels start without a
        // shortcut), so they do not produce a startup warning.
        failures
    }

    pub(super) fn update(&mut self, config: &mut ConfigStore, next: Settings) -> io::Result<()> {
        let keys = checked_keys(&next)?;
        let mut added = Vec::new();
        for (&binding, &action) in &keys {
            if !self.current.contains_key(&binding) {
                added.push((binding, self.register_new(binding, action)?));
            }
        }
        config.replace(next)?;
        self.current.retain(|key, registration| {
            if let Some(action) = keys.get(key) {
                registration.action = *action;
                true
            } else {
                false
            }
        });
        self.current.extend(added);
        Ok(())
    }
}

fn configured_keys(settings: &Settings) -> impl Iterator<Item = (Action, &str)> {
    [
        (Action::Overlay, settings.hotkey.as_str()),
        (Action::Rolling, settings.rolling_buffer_hotkey.as_str()),
        (Action::Wheel(0), settings.chat_wheel_hotkey.as_str()),
    ]
    .into_iter()
    .chain(
        settings
            .additional_chat_wheels
            .iter()
            .enumerate()
            .map(|(index, wheel)| (Action::Wheel(index + 1), wheel.hotkey.as_str())),
    )
    // A wheel kept from a bad legacy hotkey has no binding until the user sets one.
    .filter(|(_, value)| !value.trim().is_empty())
}

pub(super) fn checked_keys(settings: &Settings) -> io::Result<HashMap<Binding, Action>> {
    let mut keys = HashMap::new();
    for (action, value) in configured_keys(settings) {
        let binding = parse_hotkey(value).map_err(io::Error::other)?;
        if keys.insert(binding, action).is_some() {
            return Err(io::Error::other("快捷键互相冲突"));
        }
    }
    Ok(keys)
}
