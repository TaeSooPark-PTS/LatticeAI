use std::fmt;

use scrypt::{scrypt, Params};

const LOG_N: u8 = 14;
const R: u32 = 8;
const P: u32 = 1;
const DKLEN: usize = 64;
const SALT_BYTES: usize = 16;
pub const HASH_PREFIX: &str = "$scrypt$n=14,r=8,p=1$";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PasswordError {
    Rng(String),
    Params(String),
    Derive(String),
}

impl fmt::Display for PasswordError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PasswordError::Rng(msg) => write!(f, "OS RNG unavailable: {msg}"),
            PasswordError::Params(msg) => write!(f, "invalid scrypt parameters: {msg}"),
            PasswordError::Derive(msg) => write!(f, "scrypt derivation failed: {msg}"),
        }
    }
}

impl std::error::Error for PasswordError {}

pub fn hash_password(password: &str) -> Result<String, PasswordError> {
    let mut salt = [0u8; SALT_BYTES];
    getrandom::fill(&mut salt).map_err(|e| PasswordError::Rng(e.to_string()))?;
    let salt_hex = hex(&salt);
    let key = derive_with(password, salt_hex.as_bytes(), LOG_N, R, P)?;
    Ok(format!("{HASH_PREFIX}{salt_hex}:{}", hex(&key)))
}

fn parse_versioned(hashed: &str) -> Option<(u8, u32, u32, &str, &str)> {
    let rest = hashed.strip_prefix("$scrypt$")?;
    let (param_str, body) = rest.split_once('$')?;
    let (salt_hex, key_hex) = body.split_once(':')?;
    if !is_valid_salt_and_key(salt_hex, key_hex) {
        return None;
    }
    let mut log_n: Option<u8> = None;
    let mut r: Option<u32> = None;
    let mut p: Option<u32> = None;
    for part in param_str.split(',') {
        let (k, v) = part.split_once('=')?;
        match k {
            "n" => log_n = v.parse::<u8>().ok(),
            "r" => r = v.parse::<u32>().ok(),
            "p" => p = v.parse::<u32>().ok(),
            _ => return None,
        }
    }
    let log_n = log_n.filter(|&n| (10..=20).contains(&n))?;
    let r = r.filter(|&val| (1..=32).contains(&val))?;
    let p = p.filter(|&val| (1..=16).contains(&val))?;
    Some((log_n, r, p, salt_hex, key_hex))
}

fn is_valid_salt_and_key(salt_hex: &str, key_hex: &str) -> bool {
    salt_hex.len() == SALT_BYTES * 2
        && key_hex.len() == DKLEN * 2
        && salt_hex.bytes().all(|b| b.is_ascii_hexdigit())
        && key_hex.bytes().all(|b| b.is_ascii_hexdigit())
}

pub fn verify_password(password: &str, hashed: &str) -> bool {
    if hashed.starts_with("$scrypt$") {
        let Some((log_n, r, p, salt_hex, key_hex)) = parse_versioned(hashed) else {
            return false;
        };
        let Ok(key) = derive_with(password, salt_hex.as_bytes(), log_n, r, p) else {
            return false;
        };
        let derived = hex(&key);
        return constant_time_eq(derived.as_bytes(), key_hex.as_bytes());
    }
    let Some((salt_hex, key_hex)) = hashed.split_once(':') else {
        return false;
    };
    if salt_hex.is_empty() || key_hex.is_empty() {
        return false;
    }
    let Ok(key) = derive_with(password, salt_hex.as_bytes(), LOG_N, R, P) else {
        return false;
    };
    let derived = hex(&key);
    constant_time_eq(derived.as_bytes(), key_hex.as_bytes())
}

pub fn stored_is_hashed(stored: &str) -> bool {
    if stored.starts_with("$scrypt$") {
        return parse_versioned(stored).is_some();
    }
    match stored.split_once(':') {
        Some((salt_hex, key_hex)) => is_valid_salt_and_key(salt_hex, key_hex),
        None => false,
    }
}

fn derive_with(
    password: &str,
    salt: &[u8],
    log_n: u8,
    r: u32,
    p: u32,
) -> Result<[u8; DKLEN], PasswordError> {
    let mut out = [0u8; DKLEN];
    let params = Params::new(log_n, r, p).map_err(|e| PasswordError::Params(e.to_string()))?;
    scrypt(password.as_bytes(), salt, &params, &mut out)
        .map_err(|e| PasswordError::Derive(e.to_string()))?;
    Ok(out)
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

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

    const KNOWN_SALT: &str = "0123456789abcdef0123456789abcdef";

    #[test]
    fn a_fresh_hash_is_versioned_and_verifies() {
        let stored = hash_password("abcd1234").expect("hash_password");
        assert!(stored.starts_with(HASH_PREFIX));
        assert!(verify_password("abcd1234", &stored));
        assert!(!verify_password("abcd1235", &stored));
        let body = stored.strip_prefix(HASH_PREFIX).unwrap();
        let (salt, key) = body.split_once(':').unwrap();
        assert_eq!(salt.len(), 32);
        assert_eq!(key.len(), 128);
        assert!(stored_is_hashed(&stored));
    }

    #[test]
    fn legacy_hashes_still_verify_and_salt_is_hex_text() {
        let as_text = hex(&derive_with("abcd1234", KNOWN_SALT.as_bytes(), LOG_N, R, P).unwrap());
        let decoded: Vec<u8> = (0..16)
            .map(|index| u8::from_str_radix(&KNOWN_SALT[index * 2..index * 2 + 2], 16).unwrap())
            .collect();
        let as_bytes = hex(&derive_with("abcd1234", &decoded, LOG_N, R, P).unwrap());
        assert_ne!(as_text, as_bytes);
        let legacy = format!("{KNOWN_SALT}:{as_text}");
        assert!(stored_is_hashed(&legacy));
        assert!(verify_password("abcd1234", &legacy));
        assert!(!verify_password("wrong", &legacy));
    }

    #[test]
    fn malformed_stored_values_are_mismatches() {
        assert!(!verify_password("x", "no-colon"));
        assert!(!verify_password("x", ""));
        assert!(!verify_password("x", "salt:"));
        assert!(!verify_password("x", "$scrypt$bad$00:00"));
        assert!(!verify_password("x", "$scrypt$n=99,r=8,p=1$00:00"));
    }

    #[test]
    fn plaintext_with_colon_is_not_misclassified_as_hashed() {
        assert!(!stored_is_hashed("hunter2"));
        assert!(!stored_is_hashed(&"a".repeat(200)));
        assert!(!stored_is_hashed(&format!("user:{}", "x".repeat(80))));
        assert!(!stored_is_hashed(&format!(
            "{}:{}",
            "g".repeat(32),
            "h".repeat(128)
        )));
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
        assert!(stored_is_hashed(&format!(
            "{HASH_PREFIX}{}:{}",
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
