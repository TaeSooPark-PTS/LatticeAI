//! `latticeai/api/command_center.py` — today's briefing and Cmd+K search.
//!
//! The knowledge group of `/api/command/search` used to be permanently empty:
//! the Python original read `payload['results']` while `keyword_search` answers
//! `matches`. v11.6.0 reproduced that bug and disclosed it; v11.7.0 fixes the
//! key, which is the one place this family diverges from its oracle. The
//! reasoning, and the three fixture cases whose bodies moved, are in
//! [`search`]'s module header.

pub mod briefing;
pub mod health;
pub mod routes;
pub mod search;
pub mod store;
pub mod suggestions;

pub use routes::{router, MOUNTED};
