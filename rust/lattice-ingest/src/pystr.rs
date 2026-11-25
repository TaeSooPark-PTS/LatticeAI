pub use lattice_core::pytext::is_py_space;

pub fn py_strip(text: &str) -> &str {
    text.trim_matches(is_py_space)
}

pub fn py_suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(dot) if dot > 0 => &name[dot..],
        _ => "",
    }
}

pub fn decode_utf8_ignore(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len());
    let mut rest = bytes;
    loop {
        match std::str::from_utf8(rest) {
            Ok(text) => {
                out.push_str(text);
                return out;
            }
            Err(error) => {
                let valid = error.valid_up_to();
                out.push_str(std::str::from_utf8(&rest[..valid]).unwrap_or_default());
                let skip = error.error_len().unwrap_or(rest.len() - valid);
                rest = &rest[valid + skip..];
            }
        }
    }
}

pub fn round3(value: f64) -> f64 {
    lattice_core::pytext::round_to(value, 3)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_whitespace_includes_the_c0_separators() {
        for c in [
            ' ', '\t', '\n', '\r', '\u{0b}', '\u{0c}', '\u{85}', '\u{a0}', '\u{3000}',
        ] {
            assert!(is_py_space(c), "{c:?}");
        }
        for c in ['\u{1c}', '\u{1d}', '\u{1e}', '\u{1f}'] {
            assert!(
                is_py_space(c),
                "{c:?} is whitespace to Python but not to Unicode"
            );
            assert!(
                !c.is_whitespace(),
                "{c:?} must be the C0 case, not the Unicode one"
            );
        }
        assert!(!is_py_space('a'));
        assert!(
            !is_py_space('\u{200b}'),
            "zero-width space is not whitespace in Python"
        );
    }

    #[test]
    fn strip_removes_the_edges_and_keeps_the_middle() {
        assert_eq!(py_strip("  a \n\t b  "), "a \n\t b");
        assert_eq!(py_strip("   "), "");
        assert_eq!(py_strip(""), "");
        assert_eq!(py_strip("\u{1c}회의\u{1c}"), "회의");
    }

    #[test]
    fn suffix_matches_pathlib() {
        assert_eq!(py_suffix("a.b"), ".b");
        assert_eq!(py_suffix("archive.tar.gz"), ".gz");
        assert_eq!(py_suffix(".hidden"), "");
        assert_eq!(py_suffix("noext"), "");
        assert_eq!(py_suffix("file."), ".");
        assert_eq!(py_suffix(""), "");
        assert_eq!(py_suffix("한글.md"), ".md");
    }

    #[test]
    fn ignore_drops_invalid_bytes_instead_of_replacing_them() {
        assert_eq!(decode_utf8_ignore(b"ok"), "ok");
        assert_eq!(decode_utf8_ignore(&[0x61, 0xff, 0x62]), "ab");
        assert_eq!(decode_utf8_ignore(&[0xff, 0xfe]), "");
        assert_eq!(decode_utf8_ignore("회의".as_bytes()), "회의");
        let mut truncated = "회의".as_bytes().to_vec();
        truncated.pop();
        assert_eq!(decode_utf8_ignore(&truncated), "회");
        assert!(!decode_utf8_ignore(&[0x61, 0xff]).contains('\u{fffd}'));
    }

    #[test]
    fn round3_is_half_even_like_cpython() {
        assert_eq!(round3(1.0005).to_bits(), 1.0f64.to_bits());
        assert_eq!(round3(2.6535), 2.654);
        assert_eq!(round3(1_770_000_000.123_456), 1_770_000_000.123);
        assert_eq!(round3(0.0625), 0.062);
        assert_eq!(round3(0.0015), 0.002);
        assert_eq!(round3(0.00025).to_bits(), 0.0f64.to_bits());
        assert_eq!(round3(0.0).to_bits(), 0.0f64.to_bits());
        assert!(round3(f64::NAN).is_nan());
        assert_eq!(round3(f64::INFINITY), f64::INFINITY);
    }
}
