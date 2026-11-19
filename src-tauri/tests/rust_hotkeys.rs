use wordy::hotkey::{display_hotkey, parse_hotkey, virtual_key_for_key_name};

#[test]
fn parses_existing_config_bindings_and_aliases() {
    assert_eq!(parse_hotkey("f6").unwrap(), (0x4000, 0x75));
    assert_eq!(parse_hotkey("ctrl+shift+F6").unwrap(), (0x4006, 0x75));
    assert_eq!(
        parse_hotkey("control+page_down"),
        parse_hotkey("ctrl+page down")
    );
    assert_eq!(parse_hotkey("left windows+num 9").unwrap(), (0x4008, 0x69));
    assert_eq!(parse_hotkey("shift+?").unwrap(), (0x4004, 0xbf));
    assert_eq!(parse_hotkey("a").unwrap(), (0x4000, u32::from(b'A')));
}

#[test]
fn rejects_missing_multiple_and_invalid_main_keys() {
    for binding in [
        "ctrl+alt",
        "",
        "+++",
        "a+b",
        "ctrl+f1+f2",
        "ctrl+unknown",
        "f25",
        "num 4294967295",
    ] {
        assert!(parse_hotkey(binding).is_err(), "{binding}");
    }
}

#[test]
fn single_key_injection_and_display_keep_their_own_rules() {
    assert_eq!(virtual_key_for_key_name("Right_Control"), Some(0xa3));
    assert_eq!(virtual_key_for_key_name(" left Windows "), Some(0x5b));
    assert_eq!(virtual_key_for_key_name("ctrl+g"), None);
    assert_eq!(
        display_hotkey("control+right windows+page_down"),
        "Ctrl+Win+Page Down"
    );
    assert_eq!(display_hotkey("++"), "++");
}
