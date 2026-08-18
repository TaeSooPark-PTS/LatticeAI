//! **Content** — the bytes a run is about to write, judged before they land.
//!
//! [`crate::parse`] reads the envelope ("`write_file`, path `index.html`");
//! this group reads the **payload** and answers three questions about it:
//! is it what it claims to be ([`sanitize::validate_file_content`]), can the
//! damaged version be repaired ([`sanitize::repair_file_content`]), and if two
//! candidates survive, which is worth keeping ([`sanitize::salvage_score`]).
//! [`pydiff`] is the same subject seen after the fact: the `difflib`-identical
//! unified diff a staged change is reviewed by.
//!
//! One entry point matters above the rest: [`sanitize::sanitize_write_content`]
//! is the *single* funnel every native write passes through — the agent loop's
//! `write_file`, the platform's `/tools/write_file`, the file-generation
//! pipeline in `lattice-chat`. There is deliberately no second way in.
//!
//! ## What belongs here
//!
//! * A validator for a file type the product writes (its magic bytes, its
//!   syntax check, its "this is a refusal, not a document" detector).
//! * A repair that turns a *nearly* correct file into a correct one — an
//!   unclosed fence, a JSON body wrapped in prose, a Python file with one
//!   unbalanced bracket.
//! * Line-level text surgery over file bodies, which is what [`pydiff`] is.
//!
//! ## What must never go here
//!
//! * **Writing the file.** This group returns bytes and a verdict; the write
//!   itself belongs to [`crate::tools`], inside a
//!   [`crate::tools::sandbox::Workspace`].
//! * **Deciding whether the write is allowed.** That is [`crate::kernel`].
//!   Content answers "are these bytes any good?", never "may this run?".
//! * **Anything that needs to know which model produced the text.** Model
//!   quirks are [`crate::parse`]'s problem; by the time bytes arrive here they
//!   are just a file.
//!
//! ## Invariants
//!
//! 1. **Sanitize never invents content.** Every repair either *removes*
//!    wrapping the model added (fences, prose, channel frames) or *restores*
//!    structure the file already implies (a closing brace, a final newline).
//!    Writing a paragraph the model never produced would hand the user a
//!    document that looks authored and is not, so the honest outcome when
//!    nothing can be salvaged is to say so — not to fill the gap.
//! 2. **Every change is reported.** The returned [`sanitize::SanitizeMeta`]
//!    carries `sanitized` / `repaired` / `reason`, and the loop surfaces them.
//!    A silent fix is indistinguishable from a model that got it right, which
//!    is exactly the signal a weak-model regression would hide behind.
//! 3. **Validation is fail-closed.** A body that cannot be proved to be the
//!    claimed type is rejected or flagged, never waved through because the
//!    check was inconclusive.
//! 4. **The diff is *the* diff.** [`pydiff`] reproduces
//!    `difflib.unified_diff`'s `SequenceMatcher` output exactly, because a
//!    reviewer approving a staged change must see the same change a
//!    pre-port reviewer saw.

pub mod pydiff;
pub mod sanitize;
