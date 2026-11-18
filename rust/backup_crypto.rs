use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::{mem, ptr, slice};
use windows_sys::Win32::Foundation::{NTSTATUS, STATUS_AUTH_TAG_MISMATCH};
use windows_sys::Win32::Security::Cryptography::*;

const MAGIC: &[u8; 8] = b"WORDYBK\x02";
const HEADER_SIZE: usize = MAGIC.len() + 16 + 12;
const TAG_SIZE: usize = 16;
const CHUNK_SIZE: usize = 1024 * 1024;
const MAX_ENCRYPTED_SIZE: u64 = 2 * 1024_u64.pow(3) + 16 * 1024_u64.pow(2);
const MAX_PAYLOAD_SIZE: u64 = MAX_ENCRYPTED_SIZE - (HEADER_SIZE + TAG_SIZE) as u64;

pub struct EncryptedWriter<W> {
    output: W,
    cipher: Cipher,
    pending: Vec<u8>,
    size: u64,
}

impl<W: Write> EncryptedWriter<W> {
    pub fn new(mut output: W, password: &str) -> io::Result<Self> {
        let mut header = [0; HEADER_SIZE];
        header[..MAGIC.len()].copy_from_slice(MAGIC);
        let random = &mut header[MAGIC.len()..];
        // System-preferred RNG supplies both the independent salt and nonce.
        status(unsafe {
            BCryptGenRandom(
                ptr::null_mut(),
                random.as_mut_ptr(),
                random.len() as u32,
                BCRYPT_USE_SYSTEM_PREFERRED_RNG,
            )
        })?;
        let cipher = Cipher::new(header, password, None)?;
        output.write_all(&header)?;
        Ok(Self {
            output,
            cipher,
            pending: Vec::with_capacity(CHUNK_SIZE),
            size: 0,
        })
    }

    pub fn finish(mut self) -> io::Result<W> {
        self.cipher.transform(&mut self.pending, true)?;
        self.output.write_all(&self.pending)?;
        self.output.write_all(&self.cipher.tag)?;
        self.output.flush()?;
        Ok(self.output)
    }
}

impl<W: Write> Write for EncryptedWriter<W> {
    fn write(&mut self, mut bytes: &[u8]) -> io::Result<usize> {
        let accepted = bytes.len();
        if accepted as u64 > MAX_PAYLOAD_SIZE - self.size {
            return Err(invalid("备份过大"));
        }
        while !bytes.is_empty() {
            // Hold the final chunk until finish, even when its size is a full block.
            if self.pending.len() == CHUNK_SIZE {
                self.cipher.transform(&mut self.pending, false)?;
                self.output.write_all(&self.pending)?;
                self.pending.clear();
            }
            let length = bytes.len().min(CHUNK_SIZE - self.pending.len());
            self.pending.extend_from_slice(&bytes[..length]);
            bytes = &bytes[length..];
        }
        self.size += accepted as u64;
        Ok(accepted)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.output.flush()
    }
}

pub fn decrypt_backup(source: &Path, password: &str, temp_directory: &Path) -> io::Result<File> {
    let mut source = File::open(source)?;
    let length = source.metadata()?.len();
    let minimum = (HEADER_SIZE + TAG_SIZE) as u64;
    if length <= minimum || length > MAX_ENCRYPTED_SIZE {
        return Err(invalid("备份长度无效"));
    }
    let mut header = [0; HEADER_SIZE];
    source.read_exact(&mut header)?;
    if !header.starts_with(MAGIC) {
        return Err(invalid("不支持的加密备份格式"));
    }
    let mut tag = [0; TAG_SIZE];
    source.seek(SeekFrom::End(-(TAG_SIZE as i64)))?;
    source.read_exact(&mut tag)?;
    source.seek(SeekFrom::Start(HEADER_SIZE as u64))?;
    let mut cipher = Cipher::new(header, password, Some(tag))?;
    // The caller cannot parse or expose an entry until final authentication succeeds.
    let mut plaintext = tempfile::tempfile_in(temp_directory)?;
    let mut remaining = length - minimum;
    let mut buffer = vec![0; CHUNK_SIZE];
    loop {
        let count = remaining.min(CHUNK_SIZE as u64) as usize;
        let payload = &mut buffer[..count];
        source.read_exact(payload)?;
        remaining -= count as u64;
        cipher.transform(payload, remaining == 0)?;
        plaintext.write_all(payload)?;
        if remaining == 0 {
            break;
        }
    }
    plaintext.rewind()?;
    Ok(plaintext)
}

struct Algorithm(BCRYPT_ALG_HANDLE);

impl Algorithm {
    fn open(name: *const u16, flags: u32) -> io::Result<Self> {
        let mut handle = ptr::null_mut();
        // Name is a null-terminated BCRYPT_*_ALGORITHM constant.
        status(unsafe { BCryptOpenAlgorithmProvider(&mut handle, name, ptr::null(), flags) })?;
        Ok(Self(handle))
    }
}

impl Drop for Algorithm {
    fn drop(&mut self) {
        unsafe { BCryptCloseAlgorithmProvider(self.0, 0) };
    }
}

struct Cipher {
    handle: BCRYPT_KEY_HANDLE,
    _algorithm: Algorithm,
    header: [u8; HEADER_SIZE],
    tag: [u8; TAG_SIZE],
    mac: [u8; TAG_SIZE],
    iv: [u8; 16],
    decrypt: bool,
    auth: BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO,
}

impl Cipher {
    fn new(
        header: [u8; HEADER_SIZE],
        password: &str,
        tag: Option<[u8; TAG_SIZE]>,
    ) -> io::Result<Self> {
        let bytes = password.as_bytes();
        let password_length = u32::try_from(bytes.len()).map_err(|_| invalid("备份密码过长"))?;
        if bytes.is_empty() {
            return Err(invalid("备份密码不能为空"));
        }
        let kdf = Algorithm::open(BCRYPT_SHA256_ALGORITHM, BCRYPT_ALG_HANDLE_HMAC_FLAG)?;
        let algorithm = Algorithm::open(BCRYPT_AES_ALGORITHM, 0)?;
        let mut key = [0u8; 32];
        // Windows owns AES/GCM and PBKDF2. The version's numeric parameters are fixed here.
        let mut handle = ptr::null_mut();
        unsafe {
            status(BCryptDeriveKeyPBKDF2(
                kdf.0,
                bytes.as_ptr(),
                password_length,
                header[MAGIC.len()..].as_ptr(),
                16,
                1_200_000,
                key.as_mut_ptr(),
                key.len() as u32,
                0,
            ))?;
            let mode = slice::from_raw_parts(BCRYPT_CHAIN_MODE_GCM, "ChainingModeGCM".len() + 1);
            status(BCryptSetProperty(
                algorithm.0,
                BCRYPT_CHAINING_MODE,
                mode.as_ptr().cast(),
                mem::size_of_val(mode) as u32,
                0,
            ))?;
            let result = BCryptGenerateSymmetricKey(
                algorithm.0,
                &mut handle,
                ptr::null_mut(),
                0,
                key.as_ptr(),
                key.len() as u32,
                0,
            );
            for byte in &mut key {
                ptr::write_volatile(byte, 0);
            }
            status(result)?;
        }
        Ok(Self {
            handle,
            _algorithm: algorithm,
            header,
            tag: tag.unwrap_or_default(),
            mac: [0; TAG_SIZE],
            iv: [0; 16],
            decrypt: tag.is_some(),
            auth: BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO {
                cbSize: mem::size_of::<BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO>() as u32,
                dwInfoVersion: BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO_VERSION,
                ..Default::default()
            },
        })
    }

    fn transform(&mut self, bytes: &mut [u8], final_chunk: bool) -> io::Result<()> {
        let auth = &mut self.auth;
        // CNG updates cbAAD, cbData and its state flag between chunks. Rebind pointers after moves.
        auth.pbNonce = self.header[MAGIC.len() + 16..].as_mut_ptr();
        auth.cbNonce = 12;
        auth.pbAuthData = ptr::null_mut();
        auth.cbAuthData = 0;
        if auth.cbAAD == 0 {
            auth.pbAuthData = self.header.as_mut_ptr();
            auth.cbAuthData = HEADER_SIZE as u32;
        }
        auth.pbTag = self.tag.as_mut_ptr();
        auth.cbTag = TAG_SIZE as u32;
        auth.pbMacContext = self.mac.as_mut_ptr();
        auth.cbMacContext = TAG_SIZE as u32;
        if final_chunk {
            auth.dwFlags &= !BCRYPT_AUTH_MODE_CHAIN_CALLS_FLAG;
        } else {
            auth.dwFlags |= BCRYPT_AUTH_MODE_CHAIN_CALLS_FLAG;
        }
        let count = bytes.len() as u32;
        let payload = bytes.as_mut_ptr();
        let mut written = 0;
        let operation = if self.decrypt {
            BCryptDecrypt
        } else {
            BCryptEncrypt
        };
        // CNG permits in-place GCM. Nonfinal buffers are whole blocks by construction.
        status(unsafe {
            operation(
                self.handle,
                payload,
                count,
                ptr::from_mut(auth).cast(),
                self.iv.as_mut_ptr(),
                self.iv.len() as u32,
                payload,
                count,
                &mut written,
                0,
            )
        })?;
        if count != written {
            return Err(invalid("加密备份数据长度无效"));
        }
        Ok(())
    }
}

impl Drop for Cipher {
    fn drop(&mut self) {
        unsafe { BCryptDestroyKey(self.handle) };
    }
}

fn status(result: NTSTATUS) -> io::Result<()> {
    if result >= 0 {
        Ok(())
    } else if result == STATUS_AUTH_TAG_MISMATCH {
        Err(invalid("备份密码错误或文件已损坏"))
    } else {
        Err(io::Error::other(format!(
            "Windows 加密失败 ({result:#010x})"
        )))
    }
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};

    fn payload() -> Vec<u8> {
        (0..=255).cycle().take(2 * CHUNK_SIZE + 31).collect()
    }

    fn header() -> [u8; HEADER_SIZE] {
        let mut header = [0; HEADER_SIZE];
        header[..MAGIC.len()].copy_from_slice(MAGIC);
        for (index, byte) in header[MAGIC.len()..].iter_mut().enumerate() {
            *byte = (index as u8).wrapping_mul(31).wrapping_add(1);
        }
        header
    }

    #[test]
    fn one_shot_gcm_reads_the_chunked_writer_output() {
        let expected = payload();
        let mut writer = EncryptedWriter::new(Vec::new(), "example").unwrap();
        for chunk in expected.chunks(511) {
            writer.write_all(chunk).unwrap();
        }
        let encrypted = writer.finish().unwrap();
        let header = encrypted[..HEADER_SIZE].try_into().unwrap();
        let tag = encrypted[encrypted.len() - TAG_SIZE..].try_into().unwrap();
        let mut ciphertext = encrypted[HEADER_SIZE..encrypted.len() - TAG_SIZE].to_vec();
        let mut cipher = Cipher::new(header, "example", Some(tag)).unwrap();
        cipher.transform(&mut ciphertext, true).unwrap();
        assert_eq!(ciphertext, expected);
    }

    #[test]
    fn chunked_decryptor_reads_a_one_shot_gcm_message() {
        let expected = payload();
        let header = header();
        let mut cipher = Cipher::new(header, "example", None).unwrap();
        let mut ciphertext = expected.clone();
        cipher.transform(&mut ciphertext, true).unwrap();
        let mut encrypted = header.to_vec();
        encrypted.extend_from_slice(&ciphertext);
        encrypted.extend_from_slice(&cipher.tag);

        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("one-shot.wordybackup");
        std::fs::write(&path, encrypted).unwrap();
        let mut decoded = Vec::new();
        decrypt_backup(&path, "example", directory.path())
            .unwrap()
            .read_to_end(&mut decoded)
            .unwrap();
        assert_eq!(decoded, expected);
    }
}
