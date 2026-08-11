//! The `str` behaviours the port leans on, spelled the way Python spells them.
//!
//! Four of them, each with a call site that would be subtly wrong without it:
//! Python's whitespace set is *not* Unicode `White_Space` (it also holds the C0
//! separators), `Path.suffix` is not "everything after the last dot",
//! `errors="ignore"` **drops** invalid bytes where Rust's lossy decode
//! *replaces* them, and `str.strip()` uses the first of those.

/// True for every character Python's `str.isspace()` and `re`'s `\s` accept.
///
/// `char::is_whitespace` is the Unicode `White_Space` property; Python's is
/// that plus the C0 separators `\x1c`–`\x1f`. The difference is one character
/// class in `_CODE_BLANK_RUN_RE` and one in the prose boundary pattern, so it
/// is load-bearing rather than pedantic.
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `str.strip()` — leading and trailing [`is_py_space`], nothing else.
///
/// Note what it is *not*: `_clean_text` collapses internal whitespace runs, and
/// `typed_chunks` deliberately does not — it strips only, so chunk boundaries
/// (and therefore chunk ids) stay byte-identical to the legacy `_chunks` walk.
pub fn py_strip(text: &str) -> &str {
    text.trim_matches(is_py_space)
}

/// `pathlib.Path(name).suffix` for a final path component, lowercased by the
/// caller when the call site does.
///
/// The rule is `rfind('.')` and "not at position 0": `"a.b"` → `".b"`,
/// `".hidden"` → `""`, `"noext"` → `""`, `"file."` → `"."` (CPython 3.12+
/// stopped special-casing the trailing dot, and 3.14 is what this tree runs).
/// Byte index vs character index does not matter for the `> 0` test: a string's
/// first character always starts at byte 0, so the two agree on "is it first".
pub fn py_suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(dot) if dot > 0 => &name[dot..],
        _ => "",
    }
}

/// `bytes.decode("utf-8", errors="ignore")` — invalid sequences are **dropped**.
///
/// `String::from_utf8_lossy` inserts U+FFFD instead, which would change the
/// character offsets every chunk in this crate is indexed by. Both folder
/// ingestion (`folders.py:208`) and folder watch (`folder_watch.py:316`) read
/// files this way, so this is the decoder a parity-seeking port must use.
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
                // Safe by construction: `valid_up_to` is the length of the
                // longest valid prefix.
                out.push_str(std::str::from_utf8(&rest[..valid]).unwrap_or_default());
                let skip = error.error_len().unwrap_or(rest.len() - valid);
                rest = &rest[valid + skip..];
            }
        }
    }
}

/// CPython's `round(value, 3)` — round-half-even on the *exact* double.
///
/// The watch snapshot stores `round(st_mtime, 3)` and compares stamps for
/// equality, so a port that rounds half-away-from-zero reports a spurious
/// change on every file whose mtime lands on a tie.
pub fn round3(value: f64) -> f64 {
    if !value.is_finite() {
        return value;
    }
    format!("{value:.3}").parse::<f64>().unwrap_or(value)
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
        // A truncated multi-byte sequence at the end: dropped, not replaced.
        let mut truncated = "회의".as_bytes().to_vec();
        truncated.pop();
        assert_eq!(decode_utf8_ignore(&truncated), "회");
        assert!(!decode_utf8_ignore(&[0x61, 0xff]).contains('\u{fffd}'));
    }

    #[test]
    fn round3_is_half_even_like_cpython() {
        // Values checked against CPython 3.14 `round(x, 3)`.
        assert_eq!(round3(1.0005).to_bits(), 1.0f64.to_bits());
        assert_eq!(round3(2.6535), 2.654);
        assert_eq!(round3(1_770_000_000.123_456), 1_770_000_000.123);
        // 0.0625 is an exact tie, so the half-even rule is observable.
        assert_eq!(round3(0.0625), 0.062);
        assert_eq!(round3(0.0015), 0.002);
        assert_eq!(round3(0.00025).to_bits(), 0.0f64.to_bits());
        assert_eq!(round3(0.0).to_bits(), 0.0f64.to_bits());
        assert!(round3(f64::NAN).is_nan());
        assert_eq!(round3(f64::INFINITY), f64::INFINITY);
    }
}
