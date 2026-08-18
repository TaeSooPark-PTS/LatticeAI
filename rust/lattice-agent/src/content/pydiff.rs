//! `difflib.unified_diff`, natively — the diff a change proposal is reviewed by.
//!
//! A staged proposal carries the unified diff the Review Center renders, and
//! Python produced it with `difflib.unified_diff(before.splitlines(),
//! after.splitlines(), fromfile=…, tofile=…, lineterm="")`. That output is not
//! "a diff": it is *this* diff, produced by `SequenceMatcher`'s longest-match
//! recursion, and a proposal staged natively has to read identically to one
//! staged before the port or the reviewer sees a different change than the one
//! that will be applied.
//!
//! So this is a port of CPython's `difflib`, not a reimplementation of diffing:
//! [`SequenceMatcher`] is `Lib/difflib.py`'s algorithm including the `autojunk`
//! heuristic (which changes the output for files of 200 lines or more, so
//! leaving it out would agree with Python only on small inputs), and
//! [`unified_diff`] is its `unified_diff` generator with `lineterm=""`.
//!
//! `isjunk` is fixed at `None` because `unified_diff` never passes one; the two
//! junk-extension loops of `find_longest_match` are therefore provably no-ops
//! and are not carried. The two *popular*-extension loops are, because
//! `autojunk` really does prune elements out of `b2j`.

use std::collections::HashMap;

/// `str.splitlines()` — every boundary CPython recognises, `\r\n` as one.
///
/// Not `str::lines`: that splits on `\n` alone and keeps a lone `\r` inside the
/// line, which would put a carriage return in the middle of a diff line and
/// hash differently from Python for any CRLF file.
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0usize;
    let mut chars = text.char_indices().peekable();
    while let Some((index, ch)) = chars.next() {
        if !is_line_boundary(ch) {
            continue;
        }
        out.push(&text[start..index]);
        let mut end = index + ch.len_utf8();
        if ch == '\r' {
            if let Some((_, '\n')) = chars.peek() {
                chars.next();
                end += 1;
            }
        }
        start = end;
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// The line boundaries of `str.splitlines`.
fn is_line_boundary(ch: char) -> bool {
    matches!(
        ch,
        '\n' | '\r'
            | '\u{0b}'
            | '\u{0c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

/// One matching block: `a[i..i+size] == b[j..j+size]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Match {
    pub i: usize,
    pub j: usize,
    pub size: usize,
}

/// One opcode: what to do with `a[i1..i2]` to turn it into `b[j1..j2]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Opcode {
    pub tag: Tag,
    pub i1: usize,
    pub i2: usize,
    pub j1: usize,
    pub j2: usize,
}

/// `get_opcodes`' four tags.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tag {
    Equal,
    Replace,
    Delete,
    Insert,
}

/// `difflib.SequenceMatcher(None, a, b)` over lines.
#[derive(Debug)]
pub struct SequenceMatcher<'a> {
    a: &'a [&'a str],
    b: &'a [&'a str],
    /// `b2j` — element → its indices in `b`, popular elements purged.
    b2j: HashMap<&'a str, Vec<usize>>,
}

impl<'a> SequenceMatcher<'a> {
    /// Build the reverse index, `autojunk` included.
    pub fn new(a: &'a [&'a str], b: &'a [&'a str]) -> Self {
        let mut b2j: HashMap<&str, Vec<usize>> = HashMap::new();
        for (index, element) in b.iter().enumerate() {
            b2j.entry(element).or_default().push(index);
        }
        // `autojunk`: in a long sequence, an element appearing in more than 1%
        // of positions is treated as noise and dropped from the index. This is
        // the heuristic that makes difflib fast on source files, and it changes
        // the chosen matches — so a port without it diverges above 200 lines.
        let n = b.len();
        if n >= 200 {
            let ntest = n / 100 + 1;
            b2j.retain(|_, indices| indices.len() <= ntest);
        }
        Self { a, b, b2j }
    }

    /// `find_longest_match(alo, ahi, blo, bhi)`.
    pub fn find_longest_match(&self, alo: usize, ahi: usize, blo: usize, bhi: usize) -> Match {
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0usize);
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for i in alo..ahi {
            let mut newj2len: HashMap<usize, usize> = HashMap::new();
            if let Some(indices) = self.b2j.get(self.a[i]) {
                for &j in indices {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let k = j
                        .checked_sub(1)
                        .and_then(|prev| j2len.get(&prev).copied())
                        .unwrap_or(0)
                        + 1;
                    newj2len.insert(j, k);
                    if k > bestsize {
                        besti = i + 1 - k;
                        bestj = j + 1 - k;
                        bestsize = k;
                    }
                }
            }
            j2len = newj2len;
        }
        // Extend across elements `autojunk` pruned: they are absent from `b2j`
        // and so invisible to the loop above, but they are still equal.
        while besti > alo && bestj > blo && self.a[besti - 1] == self.b[bestj - 1] {
            besti -= 1;
            bestj -= 1;
            bestsize += 1;
        }
        while besti + bestsize < ahi
            && bestj + bestsize < bhi
            && self.a[besti + bestsize] == self.b[bestj + bestsize]
        {
            bestsize += 1;
        }
        Match {
            i: besti,
            j: bestj,
            size: bestsize,
        }
    }

    /// `get_matching_blocks()` — sorted, adjacent blocks collapsed, sentinel last.
    pub fn matching_blocks(&self) -> Vec<Match> {
        let (la, lb) = (self.a.len(), self.b.len());
        let mut queue = vec![(0usize, la, 0usize, lb)];
        let mut blocks = Vec::new();
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let found = self.find_longest_match(alo, ahi, blo, bhi);
            if found.size == 0 {
                continue;
            }
            blocks.push(found);
            if alo < found.i && blo < found.j {
                queue.push((alo, found.i, blo, found.j));
            }
            if found.i + found.size < ahi && found.j + found.size < bhi {
                queue.push((found.i + found.size, ahi, found.j + found.size, bhi));
            }
        }
        blocks.sort_unstable();

        let (mut i1, mut j1, mut k1) = (0usize, 0usize, 0usize);
        let mut merged = Vec::new();
        for block in blocks {
            if i1 + k1 == block.i && j1 + k1 == block.j {
                k1 += block.size;
            } else {
                if k1 > 0 {
                    merged.push(Match {
                        i: i1,
                        j: j1,
                        size: k1,
                    });
                }
                i1 = block.i;
                j1 = block.j;
                k1 = block.size;
            }
        }
        if k1 > 0 {
            merged.push(Match {
                i: i1,
                j: j1,
                size: k1,
            });
        }
        merged.push(Match {
            i: la,
            j: lb,
            size: 0,
        });
        merged
    }

    /// `get_opcodes()`.
    pub fn opcodes(&self) -> Vec<Opcode> {
        let (mut i, mut j) = (0usize, 0usize);
        let mut answer = Vec::new();
        for block in self.matching_blocks() {
            let tag = if i < block.i && j < block.j {
                Some(Tag::Replace)
            } else if i < block.i {
                Some(Tag::Delete)
            } else if j < block.j {
                Some(Tag::Insert)
            } else {
                None
            };
            if let Some(tag) = tag {
                answer.push(Opcode {
                    tag,
                    i1: i,
                    i2: block.i,
                    j1: j,
                    j2: block.j,
                });
            }
            i = block.i + block.size;
            j = block.j + block.size;
            if block.size > 0 {
                answer.push(Opcode {
                    tag: Tag::Equal,
                    i1: block.i,
                    i2: i,
                    j1: block.j,
                    j2: j,
                });
            }
        }
        answer
    }

    /// `get_grouped_opcodes(n)` — the hunks, each with `n` lines of context.
    pub fn grouped_opcodes(&self, n: usize) -> Vec<Vec<Opcode>> {
        let mut codes = self.opcodes();
        if codes.is_empty() {
            codes.push(Opcode {
                tag: Tag::Equal,
                i1: 0,
                i2: 1,
                j1: 0,
                j2: 1,
            });
        }
        if let Some(first) = codes.first_mut() {
            if first.tag == Tag::Equal {
                first.i1 = first.i1.max(first.i2.saturating_sub(n));
                first.j1 = first.j1.max(first.j2.saturating_sub(n));
            }
        }
        if let Some(last) = codes.last_mut() {
            if last.tag == Tag::Equal {
                last.i2 = last.i2.min(last.i1 + n);
                last.j2 = last.j2.min(last.j1 + n);
            }
        }

        let nn = n + n;
        let mut groups = Vec::new();
        let mut group: Vec<Opcode> = Vec::new();
        for code in codes {
            let mut code = code;
            if code.tag == Tag::Equal && code.i2 - code.i1 > nn {
                group.push(Opcode {
                    i2: code.i2.min(code.i1 + n),
                    j2: code.j2.min(code.j1 + n),
                    ..code
                });
                groups.push(std::mem::take(&mut group));
                code.i1 = code.i1.max(code.i2.saturating_sub(n));
                code.j1 = code.j1.max(code.j2.saturating_sub(n));
            }
            group.push(code);
        }
        if !(group.is_empty() || (group.len() == 1 && group[0].tag == Tag::Equal)) {
            groups.push(group);
        }
        groups
    }
}

/// `_format_range_unified(start, stop)`.
fn format_range(start: usize, stop: usize) -> String {
    let beginning = start + 1;
    let length = stop - start;
    if length == 1 {
        return beginning.to_string();
    }
    if length == 0 {
        return format!("{},{length}", beginning - 1);
    }
    format!("{beginning},{length}")
}

/// `difflib.unified_diff(a, b, fromfile, tofile, n=n, lineterm="")`.
///
/// Empty when the two sides are equal — `unified_diff` yields the `---`/`+++`
/// header only once a hunk exists, so "no change" is no output at all.
pub fn unified_diff(a: &[&str], b: &[&str], fromfile: &str, tofile: &str, n: usize) -> Vec<String> {
    let matcher = SequenceMatcher::new(a, b);
    let mut out: Vec<String> = Vec::new();
    for group in matcher.grouped_opcodes(n) {
        if out.is_empty() {
            out.push(format!("--- {fromfile}"));
            out.push(format!("+++ {tofile}"));
        }
        let (first, last) = (group[0], group[group.len() - 1]);
        out.push(format!(
            "@@ -{} +{} @@",
            format_range(first.i1, last.i2),
            format_range(first.j1, last.j2)
        ));
        for code in group {
            match code.tag {
                Tag::Equal => {
                    for line in &a[code.i1..code.i2] {
                        out.push(format!(" {line}"));
                    }
                }
                Tag::Replace | Tag::Delete => {
                    for line in &a[code.i1..code.i2] {
                        out.push(format!("-{line}"));
                    }
                    if code.tag == Tag::Replace {
                        for line in &b[code.j1..code.j2] {
                            out.push(format!("+{line}"));
                        }
                    }
                }
                Tag::Insert => {
                    for line in &b[code.j1..code.j2] {
                        out.push(format!("+{line}"));
                    }
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `unified_diff` over two whole texts, as `propose_file_update` calls it.
    fn diff(before: &str, after: &str, path: &str) -> Vec<String> {
        let a = splitlines(before);
        let b = splitlines(after);
        unified_diff(&a, &b, &format!("a/{path}"), &format!("b/{path}"), 3)
    }

    #[test]
    fn splitlines_is_pythons_and_not_str_lines() {
        assert!(splitlines("").is_empty());
        assert_eq!(splitlines("a\n"), vec!["a"]);
        assert_eq!(splitlines("a\nb"), vec!["a", "b"]);
        assert_eq!(splitlines("a\r\nb\r\n"), vec!["a", "b"]);
        assert_eq!(splitlines("a\rb"), vec!["a", "b"]);
        assert_eq!(splitlines("a\n\nb"), vec!["a", "", "b"]);
        // The exotic boundaries CPython honours and `str::lines` does not.
        assert_eq!(splitlines("a\u{0b}b"), vec!["a", "b"]);
        assert_eq!(splitlines("a\u{2028}b"), vec!["a", "b"]);
        assert_eq!(splitlines("한\n글"), vec!["한", "글"]);
    }

    /// The exact lists `difflib` produced for these inputs.
    ///
    /// Generated with CPython:
    /// `list(difflib.unified_diff(before.splitlines(), after.splitlines(),
    ///  fromfile="a/x", tofile="b/x", lineterm=""))`.
    #[test]
    fn the_diff_is_difflibs_line_for_line() {
        assert_eq!(
            diff(
                "Apply me as reviewed.\n",
                "Apply me as reviewed.\nPlus the approved edit.\n",
                "fixture-apply.md"
            ),
            vec![
                "--- a/fixture-apply.md",
                "+++ b/fixture-apply.md",
                "@@ -1 +1,2 @@",
                " Apply me as reviewed.",
                "+Plus the approved edit.",
            ]
        );
        assert_eq!(
            diff(
                "Conflict base.\n",
                "Conflict base.\nStaged edit.\n",
                "fixture-conflict.md"
            ),
            vec![
                "--- a/fixture-conflict.md",
                "+++ b/fixture-conflict.md",
                "@@ -1 +1,2 @@",
                " Conflict base.",
                "+Staged edit.",
            ]
        );
        // A file that did not exist: `before` is empty, so the whole body is an
        // insert and the from-range is the empty `0,0`.
        assert_eq!(
            diff("", "one\ntwo\n", "new.md"),
            vec![
                "--- a/new.md",
                "+++ b/new.md",
                "@@ -0,0 +1,2 @@",
                "+one",
                "+two",
            ]
        );
        // A replacement in the middle, with three lines of context on each side.
        let before = "1\n2\n3\n4\n5\n6\n7\n8\n9\n";
        let after = "1\n2\n3\n4\nFOUR AND A HALF\n6\n7\n8\n9\n";
        assert_eq!(
            diff(before, after, "n.txt"),
            vec![
                "--- a/n.txt",
                "+++ b/n.txt",
                "@@ -2,7 +2,7 @@",
                " 2",
                " 3",
                " 4",
                "-5",
                "+FOUR AND A HALF",
                " 6",
                " 7",
                " 8",
            ]
        );
        // Two edits far apart become two hunks.
        let before = (1..=30).map(|n| format!("{n}\n")).collect::<String>();
        let mut lines: Vec<String> = (1..=30).map(|n| n.to_string()).collect();
        lines[1] = "TWO".into();
        lines[27] = "TWENTY-EIGHT".into();
        let after = lines
            .iter()
            .map(|line| format!("{line}\n"))
            .collect::<String>();
        assert_eq!(
            diff(&before, &after, "n.txt"),
            vec![
                "--- a/n.txt",
                "+++ b/n.txt",
                "@@ -1,5 +1,5 @@",
                " 1",
                "-2",
                "+TWO",
                " 3",
                " 4",
                " 5",
                "@@ -25,6 +25,6 @@",
                " 25",
                " 26",
                " 27",
                "-28",
                "+TWENTY-EIGHT",
                " 29",
                " 30",
            ]
        );
        // Deleting the tail.
        assert_eq!(
            diff("a\nb\nc\n", "a\n", "n.txt"),
            vec![
                "--- a/n.txt",
                "+++ b/n.txt",
                "@@ -1,3 +1 @@",
                " a",
                "-b",
                "-c",
            ]
        );
        // Equal texts produce nothing at all — not even the header.
        assert!(diff("same\n", "same\n", "n.txt").is_empty());
    }

    #[test]
    fn autojunk_prunes_popular_lines_above_two_hundred() {
        // The heuristic's fingerprint, and the reason it cannot be left out.
        // One repeated line, one changed head line — and the *only* difference
        // between the two inputs below is the file's length.
        fn head_change(lines: usize) -> Vec<String> {
            let mut before: Vec<String> = vec!["head".into()];
            before.extend((1..lines).map(|_| "same".to_string()));
            let mut after = before.clone();
            after[0] = "HEAD".into();
            let text = |rows: &[String]| {
                rows.iter()
                    .map(|line| format!("{line}\n"))
                    .collect::<String>()
            };
            diff(&text(&before), &text(&after), "n.txt")
        }
        // Under 200 lines `b2j` keeps "same", so the match is found and the
        // hunk is three lines of context around one replacement.
        assert_eq!(
            head_change(100),
            vec![
                "--- a/n.txt",
                "+++ b/n.txt",
                "@@ -1,4 +1,4 @@",
                "-head",
                "+HEAD",
                " same",
                " same",
                " same",
            ]
        );
        // At 300 lines "same" appears in more than 1% of positions, `autojunk`
        // deletes it from `b2j`, `find_longest_match` sees nothing to match on
        // and difflib rewrites the whole file. CPython answers exactly this —
        // 603 lines: two headers, one `@@ -1,300 +1,300 @@`, 300 deletions and
        // 300 insertions. A port without `autojunk` would answer the 8 lines
        // above for both inputs and disagree with every real file.
        let long = head_change(300);
        assert_eq!(long.len(), 603);
        assert_eq!(long[2], "@@ -1,300 +1,300 @@");
        assert_eq!(long[3], "-head");
        assert_eq!(long[303], "+HEAD");
        assert_eq!(long[602], "+same");
    }

    #[test]
    fn the_opcode_grid_is_difflibs() {
        let a = ["q", "a", "b", "x", "c", "d"];
        let b = ["a", "b", "y", "c", "d", "f"];
        let matcher = SequenceMatcher::new(&a, &b);
        let tags: Vec<(Tag, usize, usize, usize, usize)> = matcher
            .opcodes()
            .into_iter()
            .map(|code| (code.tag, code.i1, code.i2, code.j1, code.j2))
            .collect();
        assert_eq!(
            tags,
            vec![
                (Tag::Delete, 0, 1, 0, 0),
                (Tag::Equal, 1, 3, 0, 2),
                (Tag::Replace, 3, 4, 2, 3),
                (Tag::Equal, 4, 6, 3, 5),
                (Tag::Insert, 6, 6, 5, 6),
            ]
        );
        assert_eq!(
            matcher.matching_blocks(),
            vec![
                Match {
                    i: 1,
                    j: 0,
                    size: 2
                },
                Match {
                    i: 4,
                    j: 3,
                    size: 2
                },
                Match {
                    i: 6,
                    j: 6,
                    size: 0
                },
            ]
        );
    }
}
