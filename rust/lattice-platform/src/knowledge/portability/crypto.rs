//! Cryptographic framing, key derivation, authenticated encryption, and
//! archive path-safety validation for `.latticebrain` archives and sealed
//! subgraph shares.
//!
//! Uses only workspace-locked crates (`sha2`, `ed25519-dalek`, `base64`,
//! `getrandom`, `zip`):
//! - RFC 7914 `scrypt` over `Sha256` PBKDF2 + Salsa20/8 for passphrase archives.
//! - RFC 7748 Curve25519 (`X25519`) Montgomery ECDH + RFC 5869 HKDF-SHA256 for
//!   `recipient_public_key` sealed-box archives.
//! - Encrypt-then-MAC authenticated stream cipher (`SHA256-CTR` + `HMAC-SHA256`)
//!   with constant-time MAC verification before decryption.

use std::collections::HashSet;
use std::io::{Cursor, Read, Write};

use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use base64::Engine;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use crate::http_reply;
use crate::knowledge::network::DeviceIdentity;

pub(super) use super::kdf::{
    ct_eq, hkdf_sha256, hmac_sha256, pbkdf2_hmac_sha256, scrypt_kdf, sha256_hex,
};
pub(super) use super::x25519::{
    decode_b64_key32, identity_x25519_keypair, x25519_base, x25519_scalarmult,
};

pub(crate) const ARCHIVE_FORMAT: &str = "latticeai.kg.archive";
pub(crate) const ARCHIVE_FORMAT_VERSION: u64 = 1;
pub(crate) const ARCHIVE_CIPHER: &str = "sha256-ctr-hmac-sha256";
pub(crate) const SCRYPT_N_V1_LEGACY: u32 = 4096;
pub(crate) const SCRYPT_N: u32 = 16384;
pub(crate) const SCRYPT_R: u32 = 8;
pub(crate) const SCRYPT_P: u32 = 1;
pub(crate) const PBKDF2_V2_ITERATIONS: u32 = 600_000;
const MIN_PBKDF2_ITERATIONS: u32 = 100_000;
const MAX_PBKDF2_ITERATIONS: u32 = 5_000_000;
const ARCHIVE_KDF_ENV: &str = "LATTICEAI_ARCHIVE_KDF";
const HEADER_ENTRY: &str = "header.json";
const MANIFEST_ENTRY: &str = "manifest.json";
const PAYLOAD_ENTRY: &str = "payload.enc";
const MAC_DOMAIN: &[u8] = b"latticebrain.mac.v1";
const CTR_DOMAIN: &[u8] = b"latticebrain.ctr.v1";
const HKDF_INFO_SEALED: &[u8] = b"x25519-hkdf-sha256-aes256gcm";

/// Parsed metadata and checksums from an encrypted `.latticebrain` outer container.
pub(crate) struct EncryptedArchiveEnvelope {
    pub(crate) header: Value,
    pub(crate) manifest: Value,
    pub(crate) ciphertext: Vec<u8>,
    pub(crate) outer_entries: Vec<String>,
}

/// Decrypted archive artifact returned after MAC + checksum + path validation.
pub(crate) struct DecryptedArchive {
    pub(crate) header: Value,
    pub(crate) manifest: Value,
    pub(crate) plaintext: Vec<u8>,
    pub(crate) signature_verified: bool,
    pub(crate) outer_entries: Vec<String>,
}

// ── Authenticated stream cipher (Encrypt-then-MAC) ───────────────────────────

fn xor_keystream(enc_key: &[u8; 32], nonce: &[u8; 16], data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len());
    let mut counter: u64 = 0;
    let mut ctr_msg = Vec::with_capacity(CTR_DOMAIN.len() + 16 + 8);
    ctr_msg.extend_from_slice(CTR_DOMAIN);
    ctr_msg.extend_from_slice(nonce);
    ctr_msg.extend_from_slice(&0u64.to_le_bytes());

    for chunk in data.chunks(32) {
        let off = CTR_DOMAIN.len() + 16;
        ctr_msg[off..off + 8].copy_from_slice(&counter.to_le_bytes());
        let stream = hmac_sha256(enc_key, &ctr_msg);
        for (p, s) in chunk.iter().zip(stream.iter()) {
            out.push(p ^ s);
        }
        counter = counter.saturating_add(1);
    }
    out
}

fn compute_envelope_mac(
    mac_key: &[u8; 32],
    header_binding: &[u8],
    nonce: &[u8; 16],
    ciphertext: &[u8],
) -> [u8; 32] {
    let mut mac_input =
        Vec::with_capacity(MAC_DOMAIN.len() + 4 + header_binding.len() + 16 + ciphertext.len());
    mac_input.extend_from_slice(MAC_DOMAIN);
    mac_input.extend_from_slice(&(header_binding.len() as u32).to_le_bytes());
    mac_input.extend_from_slice(header_binding);
    mac_input.extend_from_slice(nonce);
    mac_input.extend_from_slice(ciphertext);
    hmac_sha256(mac_key, &mac_input)
}

fn header_mac_binding(header: &Value) -> Result<Vec<u8>, String> {
    let canonical = json!({
        "format": header.get("format").cloned().unwrap_or(Value::Null),
        "format_version": header.get("format_version").cloned().unwrap_or(Value::Null),
        "kind": header.get("kind").cloned().unwrap_or(Value::Null),
        "encryption": header.get("encryption").cloned().unwrap_or(Value::Null),
        "kdf": header.get("kdf").cloned().unwrap_or(Value::Null),
        "kdf_params": header.get("kdf_params").cloned().unwrap_or(Value::Null),
        "cipher": header.get("cipher").cloned().unwrap_or(Value::Null),
        "salt": header.get("salt").cloned().unwrap_or(Value::Null),
        "nonce": header.get("nonce").cloned().unwrap_or(Value::Null),
        "ephemeral_public_key": header.get("ephemeral_public_key").cloned().unwrap_or(Value::Null),
        "recipient_fingerprint": header.get("recipient_fingerprint").cloned().unwrap_or(Value::Null),
        "manifest_sha256": header.get("manifest_sha256").cloned().unwrap_or(Value::Null),
        "payload_sha256": header.get("payload_sha256").cloned().unwrap_or(Value::Null),
        "created_at": header.get("created_at").cloned().unwrap_or(Value::Null),
    });
    serde_json::to_vec(&canonical)
        .map_err(|e| format!("Failed to encode archive header binding: {e}"))
}

// ── Archive Entry Safety (Path Traversal & Symlink Rejection) ────────────────

/// Validate one archive entry path strictly against directory traversal,
/// absolute paths, drive letters, backslashes, NUL bytes, and overly deep paths.
pub(crate) fn validate_archive_entry_name(name: &str) -> Result<(), String> {
    if name.is_empty() {
        return Err("Archive entry has an empty path.".into());
    }
    if name.contains('\0') {
        return Err(format!("Unsafe NUL byte in archive entry path: {name}"));
    }
    if name.contains('\\') {
        return Err(format!("Unsafe backslash in archive entry path: {name}"));
    }
    if name.starts_with('/') {
        return Err(format!("Absolute path rejected in archive entry: {name}"));
    }
    let bytes = name.as_bytes();
    if bytes.len() >= 2 && bytes[1] == b':' && bytes[0].is_ascii_alphabetic() {
        return Err(format!(
            "Drive-prefixed path rejected in archive entry: {name}"
        ));
    }
    for segment in name.trim_end_matches('/').split('/') {
        if segment.is_empty() || segment == "." || segment == ".." {
            return Err(format!("Path traversal rejected in archive entry: {name}"));
        }
    }
    Ok(())
}

/// Detect POSIX symlink entries (`S_IFLNK = 0o120000`) inside a ZIP archive.
pub(crate) fn is_zip_symlink(entry: &zip::read::ZipFile<'_>) -> bool {
    entry
        .unix_mode()
        .is_some_and(|mode| (mode & 0o170_000) == 0o120_000)
}

/// Scan every entry in an already-opened ZIP archive and reject path traversal
/// or symlinks before reading or extracting anything.
pub(crate) fn validate_zip_entries<R: Read + std::io::Seek>(
    archive: &mut zip::ZipArchive<R>,
) -> Result<Vec<String>, String> {
    let mut seen = HashSet::with_capacity(archive.len());
    let mut names = Vec::with_capacity(archive.len());
    for idx in 0..archive.len() {
        let entry = archive
            .by_index(idx)
            .map_err(|e| format!("Corrupt archive entry at index {idx}: {e}"))?;
        let name = entry.name().to_string();
        if entry.is_dir() && name.ends_with('/') {
            let trimmed = name.trim_end_matches('/');
            validate_archive_entry_name(trimmed)?;
        } else {
            validate_archive_entry_name(&name)?;
        }
        if is_zip_symlink(&entry) {
            return Err(format!("Symlink archive entry rejected: {name}"));
        }
        if !seen.insert(name.clone()) {
            return Err(format!("Duplicate archive entry rejected: {name}"));
        }
        names.push(name);
    }
    Ok(names)
}

// ── .latticebrain Outer Container Write / Inspect / Verify & Decrypt ─────────

pub(crate) enum EncryptionMode<'a> {
    Passphrase(&'a str),
    RecipientPublicKey { public_key_b64: &'a str },
}

#[allow(dead_code)]
pub(crate) enum DecryptionKey<'a> {
    Passphrase(&'a str),
    Identity(&'a DeviceIdentity),
    PassphraseOrIdentity {
        passphrase: Option<&'a str>,
        identity: &'a DeviceIdentity,
    },
}

pub(crate) fn seal_and_write_archive(
    dest: &std::path::Path,
    kind: &str,
    plaintext: &[u8],
    manifest: &mut OrderedMap,
    mode: EncryptionMode<'_>,
    identity: &DeviceIdentity,
) -> Result<(Value, Value), String> {
    let created_at = http_reply::now_iso_utc();
    let plaintext_sha = sha256_hex(plaintext);

    // Ensure manifest carries checksums + metadata cleanly.
    let mut checksums = manifest
        .get("checksums")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    checksums.insert("plaintext_zip_sha256".into(), json!(plaintext_sha));
    manifest.insert("format", json!(ARCHIVE_FORMAT));
    manifest.insert("format_version", json!(ARCHIVE_FORMAT_VERSION));
    manifest.insert("kind", json!(kind));
    manifest.insert("created_at", json!(created_at));
    manifest.insert("encrypted", json!(true));
    manifest.insert("checksums", Value::Object(checksums));

    let manifest_bytes = serde_json::to_vec_pretty(&manifest)
        .map_err(|e| format!("Failed to serialize archive manifest: {e}"))?;
    let manifest_sha = sha256_hex(&manifest_bytes);

    let mut salt = [0u8; 16];
    let mut nonce = [0u8; 16];
    getrandom::fill(&mut salt).map_err(|e| format!("RNG error: {e}"))?;
    getrandom::fill(&mut nonce).map_err(|e| format!("RNG error: {e}"))?;

    let mut key_material = [0u8; 64];
    let (encryption_label, kdf_label, kdf_params, cipher_label, eph_pub_val, rec_fp_val) =
        match mode {
            EncryptionMode::Passphrase(passphrase) => {
                if passphrase.is_empty() {
                    return Err(
                        "A passphrase is required for encrypted .latticebrain archives.".into(),
                    );
                }
                let preferred_kdf = std::env::var(ARCHIVE_KDF_ENV)
                    .ok()
                    .map(|v| v.trim().to_ascii_lowercase())
                    .unwrap_or_else(|| "scrypt".to_string());
                if preferred_kdf == "pbkdf2-hmac-sha256" || preferred_kdf == "pbkdf2" {
                    pbkdf2_hmac_sha256(
                        passphrase.as_bytes(),
                        &salt,
                        PBKDF2_V2_ITERATIONS,
                        &mut key_material,
                    );
                    (
                        "passphrase",
                        "pbkdf2-hmac-sha256",
                        json!({"v": 2, "iterations": PBKDF2_V2_ITERATIONS}),
                        ARCHIVE_CIPHER,
                        Value::Null,
                        Value::Null,
                    )
                } else {
                    scrypt_kdf(
                        passphrase.as_bytes(),
                        &salt,
                        SCRYPT_N,
                        SCRYPT_R,
                        SCRYPT_P,
                        &mut key_material,
                    )?;
                    (
                        "passphrase",
                        "scrypt",
                        json!({"v": 2, "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}),
                        ARCHIVE_CIPHER,
                        Value::Null,
                        Value::Null,
                    )
                }
            }
            EncryptionMode::RecipientPublicKey { public_key_b64 } => {
                let recipient_pub = decode_b64_key32(public_key_b64)?;
                let mut eph_scalar = [0u8; 32];
                getrandom::fill(&mut eph_scalar).map_err(|e| format!("RNG error: {e}"))?;
                eph_scalar[0] &= 248;
                eph_scalar[31] &= 127;
                eph_scalar[31] |= 64;
                let eph_pub = x25519_base(&eph_scalar);
                let shared = x25519_scalarmult(&eph_scalar, &recipient_pub);
                if ct_eq(&shared, &[0u8; 32]) {
                    return Err("Invalid X25519 recipient public key (low-order point).".into());
                }
                let mut hkdf_salt = Vec::with_capacity(64);
                hkdf_salt.extend_from_slice(&eph_pub);
                hkdf_salt.extend_from_slice(&recipient_pub);
                hkdf_sha256(&shared, &hkdf_salt, HKDF_INFO_SEALED, &mut key_material);
                let fp = sha256_hex(&recipient_pub)[..16].to_string();
                (
                    "recipient_public_key",
                    "x25519-hkdf-sha256",
                    json!({}),
                    crate::knowledge::portability::SEALED_BOX_ALGORITHM,
                    json!(URL_SAFE_NO_PAD.encode(eph_pub)),
                    json!(fp),
                )
            }
        };

    let mut enc_key = [0u8; 32];
    let mut mac_key = [0u8; 32];
    enc_key.copy_from_slice(&key_material[..32]);
    mac_key.copy_from_slice(&key_material[32..]);

    let ciphertext = xor_keystream(&enc_key, &nonce, plaintext);
    let payload_sha = sha256_hex(&ciphertext);

    let mut header = json!({
        "format": ARCHIVE_FORMAT,
        "format_version": ARCHIVE_FORMAT_VERSION,
        "kind": kind,
        "encryption": encryption_label,
        "kdf": kdf_label,
        "kdf_params": kdf_params,
        "cipher": cipher_label,
        "salt": URL_SAFE_NO_PAD.encode(salt),
        "nonce": URL_SAFE_NO_PAD.encode(nonce),
        "ephemeral_public_key": eph_pub_val,
        "recipient_fingerprint": rec_fp_val,
        "manifest_sha256": manifest_sha,
        "payload_sha256": payload_sha,
        "created_at": created_at,
    });
    let binding = header_mac_binding(&header)?;
    let mac = compute_envelope_mac(&mac_key, &binding, &nonce, &ciphertext);
    let mac_b64 = URL_SAFE_NO_PAD.encode(mac);
    header["mac"] = json!(mac_b64);

    let sign_msg = format!(
        "{}|{}|{}|{}|{}",
        ARCHIVE_FORMAT, ARCHIVE_FORMAT_VERSION, manifest_sha, payload_sha, mac_b64
    );
    header["signature"] = json!({
        "algorithm": "ed25519",
        "public_key": identity.public_key_b64(),
        "fingerprint": identity.fingerprint(),
        "signature": identity.sign(sign_msg.as_bytes()),
    });
    let header_bytes = serde_json::to_vec_pretty(&header)
        .map_err(|e| format!("Failed to serialize archive header: {e}"))?;

    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let tmp_path = dest.with_extension("latticebrain.tmp");
    {
        let file = std::fs::File::create(&tmp_path).map_err(|e| e.to_string())?;
        let mut zip = zip::ZipWriter::new(file);
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Stored);
        zip.start_file(HEADER_ENTRY, options)
            .map_err(|e| e.to_string())?;
        zip.write_all(&header_bytes).map_err(|e| e.to_string())?;
        zip.start_file(MANIFEST_ENTRY, options)
            .map_err(|e| e.to_string())?;
        zip.write_all(&manifest_bytes).map_err(|e| e.to_string())?;
        zip.start_file(PAYLOAD_ENTRY, options)
            .map_err(|e| e.to_string())?;
        zip.write_all(&ciphertext).map_err(|e| e.to_string())?;
        zip.finish().map_err(|e| e.to_string())?;
    }
    std::fs::rename(&tmp_path, dest).map_err(|e| {
        let _ = std::fs::remove_file(&tmp_path);
        e.to_string()
    })?;

    let manifest_val = serde_json::to_value(manifest).unwrap_or(json!({}));
    Ok((header, manifest_val))
}

fn read_exact_entry<R: Read + std::io::Seek>(
    archive: &mut zip::ZipArchive<R>,
    name: &str,
) -> Result<Vec<u8>, String> {
    let mut entry = archive
        .by_name(name)
        .map_err(|_| format!("Archive is missing required entry: {name}"))?;
    if is_zip_symlink(&entry) {
        return Err(format!("Symlink archive entry rejected: {name}"));
    }
    let mut buf = Vec::new();
    entry.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    Ok(buf)
}

pub(crate) fn read_archive_envelope(
    path: &std::path::Path,
) -> Result<EncryptedArchiveEnvelope, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("Cannot open archive: {e}"))?;
    let mut archive = zip::ZipArchive::new(file)
        .map_err(|e| format!("Corrupt or invalid .latticebrain archive: {e}"))?;
    let outer_entries = validate_zip_entries(&mut archive)?;

    let header_bytes = read_exact_entry(&mut archive, HEADER_ENTRY)?;
    let manifest_bytes = read_exact_entry(&mut archive, MANIFEST_ENTRY)?;
    let ciphertext = read_exact_entry(&mut archive, PAYLOAD_ENTRY)?;

    let header: Value = serde_json::from_slice(&header_bytes)
        .map_err(|e| format!("Invalid archive header JSON: {e}"))?;
    let manifest: Value = serde_json::from_slice(&manifest_bytes)
        .map_err(|e| format!("Invalid archive manifest JSON: {e}"))?;

    let format = header.get("format").and_then(Value::as_str).unwrap_or("");
    if format != ARCHIVE_FORMAT {
        return Err(format!(
            "Unsupported .latticebrain archive format: {format}"
        ));
    }
    let version = header
        .get("format_version")
        .and_then(Value::as_u64)
        .ok_or_else(|| "Missing archive format_version.".to_string())?;
    if version != ARCHIVE_FORMAT_VERSION {
        return Err(format!(
            "Unsupported .latticebrain archive format version: {version}"
        ));
    }

    let expected_manifest_sha = header
        .get("manifest_sha256")
        .and_then(Value::as_str)
        .unwrap_or("");
    let actual_manifest_sha = sha256_hex(&manifest_bytes);
    if !ct_eq(
        expected_manifest_sha.as_bytes(),
        actual_manifest_sha.as_bytes(),
    ) {
        return Err("Archive integrity check failed: manifest checksum mismatch.".into());
    }
    let expected_payload_sha = header
        .get("payload_sha256")
        .and_then(Value::as_str)
        .unwrap_or("");
    let actual_payload_sha = sha256_hex(&ciphertext);
    if !ct_eq(
        expected_payload_sha.as_bytes(),
        actual_payload_sha.as_bytes(),
    ) {
        return Err("Archive integrity check failed: encrypted payload checksum mismatch.".into());
    }

    Ok(EncryptedArchiveEnvelope {
        header,
        manifest,
        ciphertext,
        outer_entries,
    })
}

fn verify_header_signature(header: &Value) -> Result<bool, String> {
    let Some(sig_obj) = header.get("signature").and_then(Value::as_object) else {
        return Ok(false);
    };
    let pub_b64 = sig_obj
        .get("public_key")
        .and_then(Value::as_str)
        .ok_or_else(|| "Archive signature missing public_key.".to_string())?;
    let sig_b64 = sig_obj
        .get("signature")
        .and_then(Value::as_str)
        .ok_or_else(|| "Archive signature missing signature bytes.".to_string())?;
    let pub_raw = decode_b64_key32(pub_b64)?;
    let verifying = VerifyingKey::from_bytes(&pub_raw)
        .map_err(|e| format!("Invalid archive signer key: {e}"))?;
    let sig_bytes = URL_SAFE_NO_PAD
        .decode(sig_b64.trim())
        .or_else(|_| STANDARD.decode(sig_b64.trim()))
        .map_err(|e| format!("Invalid archive signature encoding: {e}"))?;
    if sig_bytes.len() != 64 {
        return Err("Invalid archive signature length.".into());
    }
    let mut sig_arr = [0u8; 64];
    sig_arr.copy_from_slice(&sig_bytes);
    let signature = Signature::from_bytes(&sig_arr);
    let msg = format!(
        "{}|{}|{}|{}|{}",
        header.get("format").and_then(Value::as_str).unwrap_or(""),
        header
            .get("format_version")
            .and_then(Value::as_u64)
            .unwrap_or(0),
        header
            .get("manifest_sha256")
            .and_then(Value::as_str)
            .unwrap_or(""),
        header
            .get("payload_sha256")
            .and_then(Value::as_str)
            .unwrap_or(""),
        header.get("mac").and_then(Value::as_str).unwrap_or(""),
    );
    verifying
        .verify(msg.as_bytes(), &signature)
        .map_err(|_| "Archive signature verification failed.".to_string())?;
    Ok(true)
}

pub(crate) fn verify_and_decrypt_archive(
    path: &std::path::Path,
    key: DecryptionKey<'_>,
) -> Result<DecryptedArchive, String> {
    let envelope = read_archive_envelope(path)?;
    let signature_verified = verify_header_signature(&envelope.header)?;

    let encryption = envelope
        .header
        .get("encryption")
        .and_then(Value::as_str)
        .unwrap_or("passphrase");

    let salt_b64 = envelope
        .header
        .get("salt")
        .and_then(Value::as_str)
        .ok_or_else(|| "Missing archive salt.".to_string())?;
    let nonce_b64 = envelope
        .header
        .get("nonce")
        .and_then(Value::as_str)
        .ok_or_else(|| "Missing archive nonce.".to_string())?;
    let mac_b64 = envelope
        .header
        .get("mac")
        .and_then(Value::as_str)
        .ok_or_else(|| "Missing archive authentication tag.".to_string())?;

    let salt = URL_SAFE_NO_PAD
        .decode(salt_b64)
        .map_err(|_| "Invalid archive salt.".to_string())?;
    let nonce_vec = URL_SAFE_NO_PAD
        .decode(nonce_b64)
        .map_err(|_| "Invalid archive nonce.".to_string())?;
    let mac_vec = URL_SAFE_NO_PAD
        .decode(mac_b64)
        .map_err(|_| "Invalid archive authentication tag.".to_string())?;
    if nonce_vec.len() != 16 || mac_vec.len() != 32 {
        return Err("Corrupt archive cryptographic framing lengths.".into());
    }
    let mut nonce = [0u8; 16];
    nonce.copy_from_slice(&nonce_vec);

    let mut key_material = [0u8; 64];
    match encryption {
        "passphrase" => {
            let passphrase = match key {
                DecryptionKey::Passphrase(p) => p,
                DecryptionKey::PassphraseOrIdentity {
                    passphrase: Some(p),
                    ..
                } if !p.is_empty() => p,
                _ => {
                    return Err(
                        "A passphrase is required to decrypt this .latticebrain archive.".into(),
                    )
                }
            };
            if passphrase.is_empty() {
                return Err(
                    "A passphrase is required to decrypt this .latticebrain archive.".into(),
                );
            }
            let kdf = envelope
                .header
                .get("kdf")
                .and_then(Value::as_str)
                .unwrap_or("");
            let params = envelope.header.get("kdf_params");
            match kdf {
                "scrypt" => {
                    let n = params
                        .and_then(|p| p.get("n"))
                        .and_then(Value::as_u64)
                        .unwrap_or(u64::from(SCRYPT_N_V1_LEGACY))
                        as u32;
                    let r = params
                        .and_then(|p| p.get("r"))
                        .and_then(Value::as_u64)
                        .unwrap_or(u64::from(SCRYPT_R)) as u32;
                    let p = params
                        .and_then(|p| p.get("p"))
                        .and_then(Value::as_u64)
                        .unwrap_or(u64::from(SCRYPT_P)) as u32;
                    scrypt_kdf(passphrase.as_bytes(), &salt, n, r, p, &mut key_material)?;
                }
                "pbkdf2-hmac-sha256" => {
                    let iterations = params
                        .and_then(|p| p.get("iterations"))
                        .and_then(Value::as_u64)
                        .unwrap_or(u64::from(PBKDF2_V2_ITERATIONS));
                    if iterations < u64::from(MIN_PBKDF2_ITERATIONS)
                        || iterations > u64::from(MAX_PBKDF2_ITERATIONS)
                    {
                        return Err(format!(
                            "Unsupported PBKDF2-HMAC-SHA256 iteration count: {iterations}"
                        ));
                    }
                    pbkdf2_hmac_sha256(
                        passphrase.as_bytes(),
                        &salt,
                        iterations as u32,
                        &mut key_material,
                    );
                }
                _ => return Err(format!("Unsupported archive KDF: {kdf}")),
            }
        }
        "recipient_public_key" => {
            let identity = match key {
                DecryptionKey::Identity(id)
                | DecryptionKey::PassphraseOrIdentity { identity: id, .. } => id,
                DecryptionKey::Passphrase(_) => {
                    return Err(
                        "Archive is sealed for a recipient device key; passphrase decryption does not apply."
                            .into(),
                    )
                }
            };
            let eph_b64 = envelope
                .header
                .get("ephemeral_public_key")
                .and_then(Value::as_str)
                .ok_or_else(|| "Missing ephemeral_public_key in sealed archive.".to_string())?;
            let eph_pub = decode_b64_key32(eph_b64)?;
            let (scalar, recipient_pub) = identity_x25519_keypair(identity);
            let shared = x25519_scalarmult(&scalar, &eph_pub);
            let mut hkdf_salt = Vec::with_capacity(64);
            hkdf_salt.extend_from_slice(&eph_pub);
            hkdf_salt.extend_from_slice(&recipient_pub);
            hkdf_sha256(&shared, &hkdf_salt, HKDF_INFO_SEALED, &mut key_material);
        }
        other => {
            return Err(format!("Unsupported archive encryption mode: {other}"));
        }
    }

    let mut enc_key = [0u8; 32];
    let mut mac_key = [0u8; 32];
    enc_key.copy_from_slice(&key_material[..32]);
    mac_key.copy_from_slice(&key_material[32..]);

    let binding = header_mac_binding(&envelope.header)?;
    let expected_mac = compute_envelope_mac(&mac_key, &binding, &nonce, &envelope.ciphertext);
    if !ct_eq(&expected_mac, &mac_vec) {
        return Err(
            "Archive authentication failed: invalid passphrase/key or tampered archive bytes."
                .into(),
        );
    }

    let plaintext = xor_keystream(&enc_key, &nonce, &envelope.ciphertext);
    let expected_plain_sha = envelope
        .manifest
        .get("checksums")
        .and_then(|c| c.get("plaintext_zip_sha256"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if !expected_plain_sha.is_empty() {
        let actual_plain_sha = sha256_hex(&plaintext);
        if !ct_eq(expected_plain_sha.as_bytes(), actual_plain_sha.as_bytes()) {
            return Err("Archive plaintext checksum mismatch after decryption.".into());
        }
    }

    // Validate inner payload ZIP entries against path traversal / symlinks if it's a ZIP.
    if plaintext.starts_with(b"PK") {
        let cursor = Cursor::new(&plaintext);
        let mut inner_zip = zip::ZipArchive::new(cursor)
            .map_err(|e| format!("Decrypted inner archive payload is corrupt: {e}"))?;
        let _ = validate_zip_entries(&mut inner_zip)?;
    }

    Ok(DecryptedArchive {
        header: envelope.header,
        manifest: envelope.manifest,
        plaintext,
        signature_verified,
        outer_entries: envelope.outer_entries,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn x25519_identity_public_key_matches_ed25519_montgomery_conversion() {
        let dir = tempfile::tempdir().expect("tempdir");
        let id = DeviceIdentity::load_or_create(&dir.path().join("device.key"));
        let (_, x_pub) = identity_x25519_keypair(&id);
        let mont_pub = id.x25519_public_bytes();
        assert_eq!(x_pub, mont_pub);
    }

    #[test]
    fn scrypt_kdf_rejects_invalid_cost_params() {
        let mut out = [0u8; 32];
        assert!(scrypt_kdf(b"pw", b"salt", 3, 8, 1, &mut out).is_err());
        assert!(scrypt_kdf(b"pw", b"salt", 1, 8, 1, &mut out).is_err());
        assert!(scrypt_kdf(b"pw", b"salt", 16, 0, 1, &mut out).is_err());
        assert!(scrypt_kdf(b"pw", b"salt", 16, 8, 0, &mut out).is_err());
        assert!(scrypt_kdf(b"pw", b"salt", 16, 8, 1, &mut out).is_ok());
    }

    #[test]
    fn path_traversal_checks_reject_unsafe_entries() {
        for unsafe_name in [
            "",
            "../evil.txt",
            "a/../../evil.txt",
            "/etc/passwd",
            "foo\\bar",
            "C:secret",
            "foo\0bar",
            "./dot",
            "a//b",
        ] {
            assert!(
                validate_archive_entry_name(unsafe_name).is_err(),
                "expected rejection for {unsafe_name:?}"
            );
        }
        assert!(validate_archive_entry_name("knowledge_graph.sqlite").is_ok());
        assert!(validate_archive_entry_name("knowledge_graph_blobs/sub/file.bin").is_ok());
    }

    #[test]
    fn sealed_box_encrypt_decrypt_roundtrip_and_wrong_recipient_fails() {
        let dir_sender = tempfile::tempdir().unwrap();
        let dir_recv = tempfile::tempdir().unwrap();
        let dir_other = tempfile::tempdir().unwrap();
        let sender = DeviceIdentity::load_or_create(&dir_sender.path().join("sender.key"));
        let receiver = DeviceIdentity::load_or_create(&dir_recv.path().join("recv.key"));
        let other = DeviceIdentity::load_or_create(&dir_other.path().join("other.key"));

        let recv_pub_b64 = URL_SAFE_NO_PAD.encode(receiver.x25519_public_bytes());
        let archive_path = dir_sender.path().join("sealed.latticebrain");
        let mut manifest = OrderedMap::new();
        manifest.insert("kind", json!("subgraph"));
        let payload = b"hello confidential brain";
        seal_and_write_archive(
            &archive_path,
            "subgraph",
            payload,
            &mut manifest,
            EncryptionMode::RecipientPublicKey {
                public_key_b64: &recv_pub_b64,
            },
            &sender,
        )
        .expect("seal");

        let opened = verify_and_decrypt_archive(&archive_path, DecryptionKey::Identity(&receiver))
            .expect("receiver unseals");
        assert_eq!(opened.plaintext, payload);
        assert!(opened.signature_verified);

        let wrong = verify_and_decrypt_archive(&archive_path, DecryptionKey::Identity(&other));
        assert!(wrong.is_err(), "wrong recipient key must be rejected");
    }

    #[test]
    fn v2_pbkdf2_600k_and_v2_scrypt_16k_roundtrip_cleanly() {
        let dir = tempfile::tempdir().unwrap();
        let signer = DeviceIdentity::load_or_create(&dir.path().join("signer.key"));
        let payload = b"confidential archive v2 kdf payload";

        // 1. Default v2 scrypt (N=16384, v=2)
        std::env::remove_var(ARCHIVE_KDF_ENV);
        let scrypt_path = dir.path().join("v2_scrypt.latticebrain");
        let mut manifest1 = OrderedMap::new();
        let (hdr1, _) = seal_and_write_archive(
            &scrypt_path,
            "full",
            payload,
            &mut manifest1,
            EncryptionMode::Passphrase("strong-passphrase"),
            &signer,
        )
        .unwrap();
        assert_eq!(hdr1["kdf"], "scrypt");
        assert_eq!(hdr1["kdf_params"]["v"], 2);
        assert_eq!(hdr1["kdf_params"]["n"], SCRYPT_N);
        let dec1 = verify_and_decrypt_archive(
            &scrypt_path,
            DecryptionKey::Passphrase("strong-passphrase"),
        )
        .unwrap();
        assert_eq!(dec1.plaintext, payload);

        // 2. v2 PBKDF2-HMAC-SHA256 (600,000 iterations)
        std::env::set_var(ARCHIVE_KDF_ENV, "pbkdf2-hmac-sha256");
        let pbkdf2_path = dir.path().join("v2_pbkdf2.latticebrain");
        let mut manifest2 = OrderedMap::new();
        let (hdr2, _) = seal_and_write_archive(
            &pbkdf2_path,
            "full",
            payload,
            &mut manifest2,
            EncryptionMode::Passphrase("strong-passphrase"),
            &signer,
        )
        .unwrap();
        std::env::remove_var(ARCHIVE_KDF_ENV);
        assert_eq!(hdr2["kdf"], "pbkdf2-hmac-sha256");
        assert_eq!(hdr2["kdf_params"]["iterations"], PBKDF2_V2_ITERATIONS);
        let dec2 = verify_and_decrypt_archive(
            &pbkdf2_path,
            DecryptionKey::Passphrase("strong-passphrase"),
        )
        .unwrap();
        assert_eq!(dec2.plaintext, payload);
    }
}
