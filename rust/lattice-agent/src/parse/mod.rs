//! **Parse** — untrusted model text in, typed values out.
//!
//! Everything a local model says arrives as prose that *might* contain a
//! decision. This group is the only place allowed to guess what it meant. Two
//! bands live here:
//!
//! * **the readers** — [`action`] (one JSON action object out of whatever was
//!   said, ten rungs of tolerance), [`channel`] (gpt-oss / gemma / harmony
//!   special-token frames), [`inference`] (which file(s) the *user* asked for);
//! * **the CPython primitives they are built on** — [`pyjson`], [`pyliteral`],
//!   [`pyshlex`], [`pystr`]. These exist because the Python originals shaped
//!   the goldens: a Rust splitter that disagrees with `shlex.split` is a hole
//!   in every argument check at once, and a byte slice where Python sliced
//!   code points changes the transcript on the first Korean sentence.
//!
//! ## What belongs here
//!
//! * A new tolerance rung for malformed model output.
//! * A new frame format some model family wraps its payload in.
//! * A port of a CPython text/lexing function the loop depends on for parity.
//!
//! ## What must never go here
//!
//! * **A decision.** Parsing says *what was said*; whether it may happen is
//!   [`crate::kernel`]. A parser that returns "blocked" has taken the kernel's
//!   job.
//! * **File content repair.** Rescuing the *body* of a file a model half-wrote
//!   is [`crate::content`]. The line is the subject: parse reads the
//!   **envelope** (which tool, which args), content reads the **payload**.
//! * **I/O.** Nothing here opens a file, spawns a process or makes a request.
//!   Every function takes `&str` and returns a value.
//!
//! ## Invariants
//!
//! 1. **Every rung names itself.** A parser that had to work to understand the
//!    model records that in the returned `repairs` list — `strip_think`,
//!    `python_literal`, `channel_frame` and the rest — so a trace can say how
//!    much help a given model needed and a weak-model regression is visible
//!    instead of silent. A new rung that repairs quietly is a bug.
//! 2. **Tolerance is ordered and terminates.** The rungs run cheapest-and-most-
//!    literal first; the first one that yields a value wins; failing every rung
//!    is an honest `None`, never a fabricated action.
//! 3. **Parity with CPython is checked, not assumed.** The primitives are
//!    pinned by the frozen goldens under `rust/fixtures/agent/`, down to the
//!    decoder's error text and character position.
//! 4. **Never invent an action.** No rung may supply a tool name, a path or an
//!    argument the model did not write. Guessing here would let a run take an
//!    action nobody asked for, and no gate downstream can tell the difference.

pub mod action;
pub mod channel;
pub mod inference;
pub mod pyjson;
pub mod pyliteral;
pub mod pyshlex;
pub mod pystr;
