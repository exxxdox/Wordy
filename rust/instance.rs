use std::sync::Mutex;
use std::{io, iter, ptr};
use windows_sys::Win32::Foundation::{CloseHandle, ERROR_ALREADY_EXISTS, GetLastError, HANDLE};
use windows_sys::Win32::System::Threading::CreateMutexW;
use windows_sys::Win32::UI::WindowsAndMessaging::{MB_OK, MB_SETFOREGROUND, MessageBoxW};

const NAME: &str = r"Local\Wordy.SingleInstance";
static ACQUIRED: Mutex<bool> = Mutex::new(false);

pub fn acquire_single_instance() -> io::Result<bool> {
    let mut acquired = ACQUIRED.lock().expect("single-instance mutex poisoned");
    if !*acquired {
        let Some(handle) = InstanceHandle::open(NAME)? else {
            return Ok(false);
        };
        // Keep the native handle through shutdown so a second process can't race final writes.
        std::mem::forget(handle);
        *acquired = true;
    }
    Ok(true)
}

pub fn show_duplicate_instance_message() {
    // TaskDialogIndirect only exists in comctl32 v6; loading it without a v6
    // manifest fails before main runs, so the duplicate prompt stays a MessageBox.
    show_native_message("Wordy 已在运行，请勿重复启动。");
}

pub fn show_native_message(text: &str) {
    let text = wide(text);
    let title = wide("Wordy");
    // Both strings are null-terminated and live for the duration of the native call.
    unsafe {
        MessageBoxW(
            ptr::null_mut(),
            text.as_ptr(),
            title.as_ptr(),
            MB_OK | MB_SETFOREGROUND,
        )
    };
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(iter::once(0)).collect()
}

struct InstanceHandle(HANDLE);

impl InstanceHandle {
    fn open(name: &str) -> io::Result<Option<Self>> {
        let name = wide(name);
        // No custom security attributes or ownership. GetLastError must follow CreateMutexW.
        let (handle, error) = unsafe {
            let handle = CreateMutexW(ptr::null(), 0, name.as_ptr());
            (handle, GetLastError())
        };
        if handle.is_null() {
            return Err(io::Error::from_raw_os_error(error as i32));
        }
        let handle = Self(handle);
        Ok((error != ERROR_ALREADY_EXISTS).then_some(handle))
    }
}

impl Drop for InstanceHandle {
    fn drop(&mut self) {
        // A successful CreateMutexW handed ownership of this handle to this value.
        unsafe { CloseHandle(self.0) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_handle_does_not_release_the_first_instance() {
        let name = format!(r"Local\Wordy.Test.SingleInstance.{}", std::process::id());
        let original = InstanceHandle::open(&name).unwrap().unwrap();
        assert!(InstanceHandle::open(&name).unwrap().is_none());
        assert!(InstanceHandle::open(&name).unwrap().is_none());
        drop(original);
        assert!(InstanceHandle::open(&name).unwrap().is_some());
    }
}
