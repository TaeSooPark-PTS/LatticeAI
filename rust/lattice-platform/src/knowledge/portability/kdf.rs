//! Constant-time comparison, SHA-256 hex digest, HMAC-SHA256, HKDF-SHA256,
//! PBKDF2-HMAC-SHA256, and RFC 7914 scrypt key derivation primitives.

use sha2::{Digest, Sha256};

const MAX_SCRYPT_N: u32 = 65_536;

pub(crate) fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

pub(crate) fn sha256_hex(data: &[u8]) -> String {
    Sha256::digest(data)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub(crate) fn hmac_sha256(key: &[u8], data: &[u8]) -> [u8; 32] {
    let mut block_key = [0u8; 64];
    if key.len() > 64 {
        let hashed = Sha256::digest(key);
        block_key[..32].copy_from_slice(&hashed);
    } else {
        block_key[..key.len()].copy_from_slice(key);
    }

    let mut ipad = [0x36u8; 64];
    let mut opad = [0x5cu8; 64];
    for i in 0..64 {
        ipad[i] ^= block_key[i];
        opad[i] ^= block_key[i];
    }

    let mut inner = Sha256::new();
    inner.update(ipad);
    inner.update(data);
    let inner_digest = inner.finalize();

    let mut outer = Sha256::new();
    outer.update(opad);
    outer.update(inner_digest);
    let out = outer.finalize();
    let mut mac = [0u8; 32];
    mac.copy_from_slice(&out);
    mac
}

pub(crate) fn pbkdf2_hmac_sha256(password: &[u8], salt: &[u8], iterations: u32, out: &mut [u8]) {
    let mut counter: u32 = 1;
    let mut offset = 0usize;
    while offset < out.len() {
        let mut msg = Vec::with_capacity(salt.len() + 4);
        msg.extend_from_slice(salt);
        msg.extend_from_slice(&counter.to_be_bytes());
        let mut u = hmac_sha256(password, &msg);
        let mut t = u;
        for _ in 1..iterations {
            u = hmac_sha256(password, &u);
            for (dst, src) in t.iter_mut().zip(u.iter()) {
                *dst ^= src;
            }
        }
        let take = (out.len() - offset).min(32);
        out[offset..offset + take].copy_from_slice(&t[..take]);
        offset += take;
        counter = counter.saturating_add(1);
    }
}

pub(crate) fn hkdf_sha256(ikm: &[u8], salt: &[u8], info: &[u8], out: &mut [u8]) {
    let actual_salt = if salt.is_empty() {
        &[0u8; 32][..]
    } else {
        salt
    };
    let prk = hmac_sha256(actual_salt, ikm);
    let mut t: Vec<u8> = Vec::new();
    let mut counter: u8 = 1;
    let mut offset = 0usize;
    while offset < out.len() {
        let mut msg = Vec::with_capacity(t.len() + info.len() + 1);
        msg.extend_from_slice(&t);
        msg.extend_from_slice(info);
        msg.push(counter);
        let block = hmac_sha256(&prk, &msg);
        let take = (out.len() - offset).min(32);
        out[offset..offset + take].copy_from_slice(&block[..take]);
        t = block.to_vec();
        offset += take;
        counter = counter.wrapping_add(1);
    }
}

#[inline]
fn salsa20_8_core(b: &mut [u32; 16]) {
    let mut x = *b;
    for _ in (0..8).step_by(2) {
        macro_rules! qr {
            ($a:expr, $b:expr, $c:expr, $d:expr) => {
                x[$b] ^= (x[$a].wrapping_add(x[$d])).rotate_left(7);
                x[$c] ^= (x[$b].wrapping_add(x[$a])).rotate_left(9);
                x[$d] ^= (x[$c].wrapping_add(x[$b])).rotate_left(13);
                x[$a] ^= (x[$d].wrapping_add(x[$c])).rotate_left(18);
            };
        }
        qr!(0, 4, 8, 12);
        qr!(5, 9, 13, 1);
        qr!(10, 14, 2, 6);
        qr!(15, 3, 7, 11);
        qr!(0, 1, 2, 3);
        qr!(5, 6, 7, 4);
        qr!(10, 11, 8, 9);
        qr!(15, 12, 13, 14);
    }
    for i in 0..16 {
        b[i] = b[i].wrapping_add(x[i]);
    }
}

fn scrypt_block_mix(b: &mut [u32], tmp: &mut [u32], r: usize) {
    let blocks = 2 * r;
    let mut x = [0u32; 16];
    let last_offset = (blocks - 1) * 16;
    x.copy_from_slice(&b[last_offset..last_offset + 16]);
    for i in 0..blocks {
        let off = i * 16;
        for j in 0..16 {
            x[j] ^= b[off + j];
        }
        salsa20_8_core(&mut x);
        let dst = if i % 2 == 0 {
            (i / 2) * 16
        } else {
            (r + i / 2) * 16
        };
        tmp[dst..dst + 16].copy_from_slice(&x);
    }
    b.copy_from_slice(tmp);
}

fn scrypt_romix(b: &mut [u32], n: usize, r: usize) {
    let block_words = 32 * r;
    let mut v = vec![0u32; n * block_words];
    let mut tmp = vec![0u32; block_words];
    for i in 0..n {
        v[i * block_words..(i + 1) * block_words].copy_from_slice(b);
        scrypt_block_mix(b, &mut tmp, r);
    }
    let mask = n - 1;
    for _ in 0..n {
        let j = (b[(2 * r - 1) * 16] as usize) & mask;
        let vj = &v[j * block_words..(j + 1) * block_words];
        for (dst, src) in b.iter_mut().zip(vj.iter()) {
            *dst ^= src;
        }
        scrypt_block_mix(b, &mut tmp, r);
    }
}

/// RFC 7914 memory-hard scrypt KDF.
pub(crate) fn scrypt_kdf(
    password: &[u8],
    salt: &[u8],
    n: u32,
    r: u32,
    p: u32,
    out: &mut [u8],
) -> Result<(), String> {
    if n < 2 || !n.is_power_of_two() || n > MAX_SCRYPT_N {
        return Err(format!("Unsupported scrypt N parameter: {n}"));
    }
    if r == 0 || r > 32 || p == 0 || p > 8 {
        return Err(format!("Unsupported scrypt r/p parameters: r={r}, p={p}"));
    }
    let block_bytes = 128usize * (r as usize);
    let mut b_bytes = vec![0u8; (p as usize) * block_bytes];
    pbkdf2_hmac_sha256(password, salt, 1, &mut b_bytes);

    let block_words = 32usize * (r as usize);
    for chunk in b_bytes.chunks_exact_mut(block_bytes) {
        let mut words = vec![0u32; block_words];
        for (idx, w) in words.iter_mut().enumerate() {
            let off = idx * 4;
            *w = u32::from_le_bytes([chunk[off], chunk[off + 1], chunk[off + 2], chunk[off + 3]]);
        }
        scrypt_romix(&mut words, n as usize, r as usize);
        for (idx, w) in words.iter().enumerate() {
            let off = idx * 4;
            chunk[off..off + 4].copy_from_slice(&w.to_le_bytes());
        }
    }

    pbkdf2_hmac_sha256(password, &b_bytes, 1, out);
    Ok(())
}
