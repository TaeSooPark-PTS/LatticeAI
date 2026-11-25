use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use base64::Engine;

use crate::knowledge::network::DeviceIdentity;

type FieldElem = [i64; 16];

fn fe_carry(o: &mut FieldElem) {
    for i in 0..16 {
        o[i] += 1 << 16;
        let c = o[i] >> 16;
        o[(i + 1) * usize::from(i < 15)] += c - 1 + 37 * (c - 1) * i64::from(i == 15);
        o[i] -= c << 16;
    }
}

fn fe_sel(p: &mut FieldElem, q: &mut FieldElem, b: i64) {
    let c = !(b - 1);
    for i in 0..16 {
        let t = c & (p[i] ^ q[i]);
        p[i] ^= t;
        q[i] ^= t;
    }
}

fn fe_unpack(i: &[u8; 32]) -> FieldElem {
    let mut o = [0i64; 16];
    for k in 0..16 {
        o[k] = i64::from(i[2 * k]) + (i64::from(i[2 * k + 1]) << 8);
    }
    o[15] &= 0x7fff;
    o
}

fn fe_pack(n: &FieldElem) -> [u8; 32] {
    let mut m = [0i64; 16];
    let mut t = *n;
    fe_carry(&mut t);
    fe_carry(&mut t);
    fe_carry(&mut t);
    for _ in 0..2 {
        m[0] = t[0] - 0xffed;
        for i in 1..15 {
            m[i] = t[i] - 0xffff - ((m[i - 1] >> 16) & 1);
            m[i - 1] &= 0xffff;
        }
        m[15] = t[15] - 0x7fff - ((m[14] >> 16) & 1);
        let b = (m[15] >> 16) & 1;
        m[14] &= 0xffff;
        fe_sel(&mut t, &mut m, 1 - b);
    }
    let mut o = [0u8; 32];
    for i in 0..16 {
        o[2 * i] = (t[i] & 0xff) as u8;
        o[2 * i + 1] = ((t[i] >> 8) & 0xff) as u8;
    }
    o
}

fn fe_add(a: &FieldElem, b: &FieldElem) -> FieldElem {
    let mut o = [0i64; 16];
    for i in 0..16 {
        o[i] = a[i] + b[i];
    }
    o
}

fn fe_sub(a: &FieldElem, b: &FieldElem) -> FieldElem {
    let mut o = [0i64; 16];
    for i in 0..16 {
        o[i] = a[i] - b[i];
    }
    o
}

fn fe_mul(a: &FieldElem, b: &FieldElem) -> FieldElem {
    let mut t = [0i64; 31];
    for i in 0..16 {
        for j in 0..16 {
            t[i + j] += a[i] * b[j];
        }
    }
    for i in 0..15 {
        t[i] += 38 * t[i + 16];
    }
    let mut o = [0i64; 16];
    o.copy_from_slice(&t[..16]);
    fe_carry(&mut o);
    fe_carry(&mut o);
    o
}

fn fe_sq(a: &FieldElem) -> FieldElem {
    fe_mul(a, a)
}

fn fe_inv(i: &FieldElem) -> FieldElem {
    let mut c = *i;
    for a in (0..=253).rev() {
        c = fe_sq(&c);
        if a != 2 && a != 4 {
            c = fe_mul(&c, i);
        }
    }
    c
}

pub(crate) fn x25519_scalarmult(n: &[u8; 32], p: &[u8; 32]) -> [u8; 32] {
    let mut z = *n;
    z[0] &= 248;
    z[31] &= 127;
    z[31] |= 64;

    let x = fe_unpack(p);
    let mut a: FieldElem = [0; 16];
    let mut b = x;
    let mut c: FieldElem = [0; 16];
    let mut d: FieldElem = [0; 16];
    a[0] = 1;
    d[0] = 1;
    let mut fe121665: FieldElem = [0; 16];
    fe121665[0] = 0xdb41;
    fe121665[1] = 1;

    for i in (0..=254).rev() {
        let r = i64::from((z[i >> 3] >> (i & 7)) & 1);
        fe_sel(&mut a, &mut b, r);
        fe_sel(&mut c, &mut d, r);
        let mut e = fe_add(&a, &c);
        a = fe_sub(&a, &c);
        c = fe_add(&b, &d);
        b = fe_sub(&b, &d);
        d = fe_sq(&e);
        let f = fe_sq(&a);
        a = fe_mul(&c, &a);
        c = fe_mul(&b, &e);
        e = fe_add(&a, &c);
        a = fe_sub(&a, &c);
        b = fe_sq(&a);
        c = fe_sub(&d, &f);
        a = fe_mul(&c, &fe121665);
        a = fe_add(&a, &d);
        c = fe_mul(&c, &a);
        a = fe_mul(&d, &f);
        d = fe_mul(&b, &x);
        b = fe_sq(&e);
        fe_sel(&mut a, &mut b, r);
        fe_sel(&mut c, &mut d, r);
    }

    let inv_c = fe_inv(&c);
    let out = fe_mul(&a, &inv_c);
    fe_pack(&out)
}

pub(crate) fn x25519_base(scalar: &[u8; 32]) -> [u8; 32] {
    let mut nine = [0u8; 32];
    nine[0] = 9;
    x25519_scalarmult(scalar, &nine)
}

pub(crate) fn identity_x25519_keypair(identity: &DeviceIdentity) -> ([u8; 32], [u8; 32]) {
    let mut scalar = identity.x25519_secret_scalar();
    scalar[0] &= 248;
    scalar[31] &= 127;
    scalar[31] |= 64;
    let pubkey = x25519_base(&scalar);
    (scalar, pubkey)
}

pub(crate) fn decode_b64_key32(raw: &str) -> Result<[u8; 32], String> {
    let trimmed = raw.trim();
    let bytes = URL_SAFE_NO_PAD
        .decode(trimmed)
        .or_else(|_| STANDARD.decode(trimmed))
        .map_err(|e| format!("Invalid base64 public key: {e}"))?;
    if bytes.len() != 32 {
        return Err(format!(
            "Expected 32-byte key material, got {} bytes",
            bytes.len()
        ));
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(&bytes);
    Ok(out)
}
