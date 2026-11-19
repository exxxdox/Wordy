#![cfg(windows)]

use std::io::{Read, Write};
use wordy::backup_crypto::{EncryptedWriter, decrypt_backup};

fn python_backup_fixture() -> Vec<u8> {
    // Produced by Python hashlib.pbkdf2_hmac and cryptography AESGCM with synthetic inputs.
    let mut fixture = b"WORDYBK\x02".to_vec();
    fixture.extend(0..28);
    let ciphertext = "bd37d595f5bce5ae520659299ec8399caf074c6ee2db6993e705f2ecaf723f948603d6d2ca7d6c748880e6bae37980fa5c629286c3eaa3e20f60ec1bd41d00";
    fixture.extend(
        (0..ciphertext.len())
            .step_by(2)
            .map(|offset| u8::from_str_radix(&ciphertext[offset..offset + 2], 16).unwrap()),
    );
    fixture
}

#[test]
fn reads_the_python_format_and_preserves_password_whitespace() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("example.wordybackup");
    std::fs::write(&source, python_backup_fixture()).unwrap();
    let mut decoded = Vec::new();
    decrypt_backup(&source, " 测试 ", directory.path())
        .unwrap()
        .read_to_end(&mut decoded)
        .unwrap();
    assert_eq!(decoded, (0..47).collect::<Vec<_>>());
    assert!(decrypt_backup(&source, "测试", directory.path()).is_err());
}

#[test]
fn streams_multiple_chunks_and_requires_the_final_tag() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("example.wordybackup");
    let data: Vec<_> = (0..=255).cycle().take(2 * 1024 * 1024 + 31).collect();
    let mut writer = EncryptedWriter::new(Vec::new(), "example").unwrap();
    for chunk in data.chunks(511) {
        writer.write_all(chunk).unwrap();
    }
    let mut encrypted = writer.finish().unwrap();
    assert_eq!(encrypted.len(), 36 + data.len() + 16);
    std::fs::write(&source, &encrypted).unwrap();
    let mut decoded = Vec::new();
    decrypt_backup(&source, "example", directory.path())
        .unwrap()
        .read_to_end(&mut decoded)
        .unwrap();
    assert_eq!(decoded, data);
    encrypted[1024 * 1024 + 37] ^= 1;
    std::fs::write(&source, encrypted).unwrap();
    assert!(decrypt_backup(&source, "example", directory.path()).is_err());
}
