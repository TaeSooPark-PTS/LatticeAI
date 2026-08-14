//! The stored-password KDF, ported from `latticeai/core/security.py`.
//!
//! `hashlib.scrypt(password, salt=salt.encode(), n=16384, r=8, p=1)` with
//! CPython's default `dklen=64`, stored as `"{salt_hex}:{key_hex}"`. Two
//! details decide whether an existing `users.json` still opens:
//!
//! * the salt is fed to scrypt as the **ASCII hex text**, not the 16 bytes it
//!   spells — `salt.encode()` encodes the hex string;
//! * `dklen` is 64, which is CPython's default and not scrypt's usual 32.

use scrypt::{scrypt, Params};

/// `n = 2^14`, the Python `n=16384`.
const LOG_N: u8 = 14;
const R: u32 = 8;
const P: u32 = 1;
const DKLEN: usize = 64;
/// `secrets.token_hex(16)` — 16 random bytes rendered as 32 hex characters.
const SALT_BYTES: usize = 16;

/// Derive the stored form of a new password: `"{salt_hex}:{key_hex}"`.
pub fn hash_password(password: &str) -> String {
    let mut salt = [0u8; SALT_BYTES];
    getrandom::fill(&mut salt).expect("the OS RNG is required to set a password");
    let salt_hex = hex(&salt);
    let key = derive(password, salt_hex.as_bytes());
    format!("{salt_hex}:{}", hex(&key))
}

/// Whether `password` produces the stored digest. Any malformed stored value
/// is a mismatch, never an error — the Python original swallows everything.
pub fn verify_password(password: &str, hashed: &str) -> bool {
    let Some((salt_hex, key_hex)) = hashed.split_once(':') else {
        return false;
    };
    let derived = hex(&derive(password, salt_hex.as_bytes()));
    constant_time_eq(derived.as_bytes(), key_hex.as_bytes())
}

/// `verify_and_migrate_password`'s classification of a stored value.
///
/// The Python helper reads: a value containing `":"` and longer than 64
/// characters is a scrypt digest; anything else is a pre-hash plaintext that
/// must be compared directly and then upgraded in place.
pub fn stored_is_hashed(stored: &str) -> bool {
    stored.contains(':') && stored.chars().count() > 64
}

fn derive(password: &str, salt: &[u8]) -> [u8; DKLEN] {
    let mut out = [0u8; DKLEN];
    let params = Params::new(LOG_N, R, P, DKLEN).expect("scrypt parameters are constants");
    // `scrypt` only fails on an output length its parameters disallow, which
    // `Params::new` has already validated.
    scrypt(password.as_bytes(), salt, &params, &mut out)
        .expect("scrypt output length matches the parameters");
    out
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// `secrets.compare_digest` over two hex strings.
fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    let mut diff = 0u8;
    for (a, b) in left.iter().zip(right.iter()) {
        diff |= a ^ b;
    }
    diff == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Recorded from CPython:
    /// `hashlib.scrypt(b"abcd1234", salt=b"0123456789abcdef0123456789abcdef",
    ///                 n=16384, r=8, p=1).hex()`
    const KNOWN_SALT: &str = "0123456789abcdef0123456789abcdef";

    #[test]
    fn a_fresh_hash_verifies_and_a_wrong_password_does_not() {
        let stored = hash_password("abcd1234");
        assert!(verify_password("abcd1234", &stored));
        assert!(!verify_password("abcd1235", &stored));
        let (salt, key) = stored.split_once(':').unwrap();
        assert_eq!(salt.len(), 32);
        assert_eq!(key.len(), 128);
        assert!(stored_is_hashed(&stored));
    }

    #[test]
    fn the_salt_is_hashed_as_its_hex_text() {
        // Feeding the decoded 16 bytes instead of the 32 hex characters would
        // change every digest, so pin the distinction explicitly.
        let as_text = hex(&derive("abcd1234", KNOWN_SALT.as_bytes()));
        let decoded: Vec<u8> = (0..16)
            .map(|index| u8::from_str_radix(&KNOWN_SALT[index * 2..index * 2 + 2], 16).unwrap())
            .collect();
        let as_bytes = hex(&derive("abcd1234", &decoded));
        assert_ne!(as_text, as_bytes);
        assert!(verify_password(
            "abcd1234",
            &format!("{KNOWN_SALT}:{as_text}")
        ));
    }

    #[test]
    fn malformed_stored_values_are_mismatches() {
        assert!(!verify_password("x", "no-colon"));
        assert!(!verify_password("x", ""));
        assert!(!verify_password("x", "salt:"));
    }

    #[test]
    fn plaintext_and_hashed_are_told_apart_the_python_way() {
        assert!(!stored_is_hashed("hunter2"));
        assert!(!stored_is_hashed(&"a".repeat(200)));
        assert!(!stored_is_hashed(&format!(
            "{}:{}",
            "a".repeat(30),
            "b".repeat(30)
        )));
        assert!(stored_is_hashed(&format!(
            "{}:{}",
            "a".repeat(32),
            "b".repeat(128)
        )));
    }

    #[test]
    fn constant_time_compare_rejects_length_mismatch() {
        assert!(!constant_time_eq(b"abc", b"abcd"));
        assert!(constant_time_eq(b"abc", b"abc"));
    }
}
