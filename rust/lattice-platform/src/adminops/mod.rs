//! **Admin ops** — operating this install: what happened, what is risky, and
//! how it is being used.
//!
//! [`admin`] is the console and, more importantly, the crate's **audit
//! writer**: [`admin::append_audit_event`] and `load_audit_log` are the single
//! path to the audit file, which is why `lattice-chat` and `lattice-host` call
//! into this module from outside rather than growing writers of their own.
//! [`security_dashboard`] is the same record read as a security question —
//! sensitive-message classification, secret redaction, the export. And
//! [`funnel_metrics`] is the install's own product telemetry: the UX funnel
//! counters, kept local in `funnel_metrics.json`.
//!
//! Everything here is *about* the product rather than *of* it. Nothing in this
//! domain is on a user's critical path; all of it is how an owner finds out
//! what their install did.
//!
//! ## What belongs here
//!
//! * An audit event, its classification, or its redaction rule.
//! * A security view over the audit record, or an export of one.
//! * A counter that measures how the product is being used.
//! * An owner-only console route.
//!
//! ## What must never go here
//!
//! * **A second audit writer.** One appender, one file, one format. A family
//!   that writes its own audit line will produce a log that is *almost*
//!   chronological, which is worse than one that is not.
//! * **A document generator.** The spreadsheet export asks the compute seam
//!   (`POST /worker/render/xlsx`) for the workbook's bytes and writes the
//!   response. A Rust xlsx crate here would fork the document matrix that the
//!   Python worker owns.
//! * **Telemetry that leaves the machine.** [`funnel_metrics`] is a local
//!   counter file. This is a local-first product and an outbound metric would
//!   contradict its whole premise.
//! * **An HTTP helper other domains reach for by accident.** [`admin`] happens
//!   to export `json_ok` / `now_iso` / `language_from`; that is a historical
//!   convenience, not an invitation. A new shared helper belongs beside its
//!   users, not deeper in this module.
//!
//! ## Invariants
//!
//! 1. **Redaction happens before persistence, not before display.** A secret
//!    that reached the audit file is already leaked; the classifier runs on the
//!    way in.
//! 2. **Append-only.** Audit entries are added and never rewritten. A surface
//!    that can edit the record cannot be used to trust it.
//! 3. **Sensitivity classification is Python-compatible.** The lookbehind
//!    patterns are matched with `fancy-regex` specifically so the Rust verdict
//!    equals the Python one; a "simpler" regex that drops a lookbehind silently
//!    reclassifies real traffic.
//! 4. **Owner-only means checked here.** Every route in this domain carries its
//!    own admin check. Relying on the caller having checked is how a console
//!    route becomes a public one after a refactor.

pub mod admin;
pub mod funnel_metrics;
pub mod security_dashboard;
