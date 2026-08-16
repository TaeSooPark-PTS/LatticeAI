use std::time::{SystemTime, UNIX_EPOCH};

use axum::http::{header, HeaderMap, HeaderValue};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use sha2::{Digest, Sha256};

use super::*;

pub fn invite_authorized(config: &StaticUiConfig, headers: &HeaderMap) -> bool {
    if !config.invite_gate_enabled {
        return true;
    }
    let cookie = cookie_value(headers, INVITE_COOKIE_NAME);
    verify_invite_cookie(
        cookie.as_deref(),
        &config.invite_cookie_secret,
        now_seconds(),
    )
}

/// `Set-Cookie` for a freshly signed claim, in Starlette's attribute order.
pub(crate) fn issue_invite_cookie(config: &StaticUiConfig) -> Option<HeaderValue> {
    let nonce = token_urlsafe(24)?;
    let value = sign_invite_cookie(
        &config.invite_cookie_secret,
        now_seconds() + INVITE_COOKIE_TTL_SECONDS,
        &nonce,
    );
    let mut cookie = format!(
        "{INVITE_COOKIE_NAME}={value}; HttpOnly; Max-Age={INVITE_COOKIE_TTL_SECONDS}; Path=/; SameSite=lax"
    );
    if config.secure_cookies {
        cookie.push_str("; Secure");
    }
    HeaderValue::from_str(&cookie).ok()
}

/// `_sign_invite_cookie` — `v1.<expiry>.<nonce>.<hmac-sha256 hex>`.
///
/// The signed payload is `<expiry>.<nonce>`, so neither half can be moved
/// between cookies, and the nonce means two claims issued in the same second
/// are still different strings.
pub fn sign_invite_cookie(secret: &str, expires_at: i64, nonce: &str) -> String {
    let payload = format!("{expires_at}.{nonce}");
    let signature = hmac_sha256_hex(secret.as_bytes(), payload.as_bytes());
    format!("v1.{payload}.{signature}")
}

/// `_verify_invite_cookie` — version, expiry and signature, trusting no claim.
///
/// Deliberately branch-for-branch with Python, including that expiry is
/// *exclusive* (`expires_at <= now` is dead) and that a malformed value is a
/// refusal rather than an error.
pub fn verify_invite_cookie(value: Option<&str>, secret: &str, now: i64) -> bool {
    let value = match value {
        Some(value) if !value.is_empty() => value,
        _ => return false,
    };
    if secret.is_empty() {
        return false;
    }
    // `value.split(".", 3)` in Python: four fields, the last of which may itself
    // contain dots. A nonce is base64url and cannot, but the parser is the
    // contract, not the alphabet.
    let mut parts = value.splitn(4, '.');
    let (version, raw_expiry, nonce, supplied) =
        match (parts.next(), parts.next(), parts.next(), parts.next()) {
            (Some(version), Some(expiry), Some(nonce), Some(signature)) => {
                (version, expiry, nonce, signature)
            }
            _ => return false,
        };
    let expires_at: i64 = match raw_expiry.parse() {
        Ok(parsed) => parsed,
        Err(_) => return false,
    };
    if version != "v1" || nonce.is_empty() || expires_at <= now {
        return false;
    }
    let expected = hmac_sha256_hex(
        secret.as_bytes(),
        format!("{expires_at}.{nonce}").as_bytes(),
    );
    constant_time_eq(supplied.as_bytes(), expected.as_bytes())
}

/// HMAC-SHA256, hex — `hmac.new(secret, payload, hashlib.sha256).hexdigest()`.
fn hmac_sha256_hex(key: &[u8], message: &[u8]) -> String {
    const BLOCK: usize = 64;
    let mut padded = [0u8; BLOCK];
    if key.len() > BLOCK {
        padded[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        padded[..key.len()].copy_from_slice(key);
    }
    let mut inner_key = [0x36u8; BLOCK];
    let mut outer_key = [0x5cu8; BLOCK];
    for index in 0..BLOCK {
        inner_key[index] ^= padded[index];
        outer_key[index] ^= padded[index];
    }
    let inner = Sha256::new()
        .chain_update(inner_key)
        .chain_update(message)
        .finalize();
    let digest = Sha256::new()
        .chain_update(outer_key)
        .chain_update(inner)
        .finalize();
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// `secrets.compare_digest` — length-revealing, content-blind.
pub(crate) fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right.iter())
        .fold(0u8, |accumulator, (a, b)| accumulator | (a ^ b))
        == 0
}

/// `secrets.token_urlsafe(bytes)` — random bytes, base64url, unpadded.
pub(crate) fn token_urlsafe(bytes: usize) -> Option<String> {
    let mut buffer = vec![0u8; bytes];
    getrandom::fill(&mut buffer).ok()?;
    Some(URL_SAFE_NO_PAD.encode(&buffer))
}

/// Starlette's lenient cookie split: last value wins, quotes stripped, a pair
/// without `=` counts as a nameless cookie rather than ending the parse.
///
/// A stricter parser would fail *open* here — the invite gate would stop seeing
/// a claim that a browser is quite happy to send alongside a malformed one.
pub fn cookie_value(headers: &HeaderMap, name: &str) -> Option<String> {
    let mut found = None;
    for header in headers.get_all(header::COOKIE).iter() {
        let raw = match header.to_str() {
            Ok(raw) => raw,
            Err(_) => continue,
        };
        for chunk in raw.split(';') {
            let (key, value) = match chunk.split_once('=') {
                Some((key, value)) => (key.trim(), value.trim()),
                None => ("", chunk.trim()),
            };
            if key == name {
                found = Some(unquote(value));
            }
        }
    }
    found
}

fn unquote(value: &str) -> String {
    if value.len() >= 2 && value.starts_with('"') && value.ends_with('"') {
        value[1..value.len() - 1].to_string()
    } else {
        value.to_string()
    }
}

/// The first value for `key` in a raw query string, percent-decoded.
///
/// Starlette's `QueryParams.get` returns the first occurrence; FastAPI hands
/// that to the handler decoded.
pub(crate) fn first_query_value(query: &str, key: &str) -> Option<String> {
    query.split('&').find_map(|pair| {
        let (name, value) = pair.split_once('=')?;
        (percent_decode(name) == key).then(|| percent_decode(value))
    })
}

/// Form-decoding as a query string carries it: `+` is a space, `%XX` is a byte.
fn percent_decode(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                out.push(b' ');
                index += 1;
            }
            b'%' if index + 2 < bytes.len() => {
                match u8::from_str_radix(&raw[index + 1..index + 3], 16) {
                    Ok(byte) => {
                        out.push(byte);
                        index += 3;
                    }
                    Err(_) => {
                        out.push(b'%');
                        index += 1;
                    }
                }
            }
            byte => {
                out.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn now_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs() as i64)
        .unwrap_or_default()
}
