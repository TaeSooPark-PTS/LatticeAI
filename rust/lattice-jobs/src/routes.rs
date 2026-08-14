//! The two `/host/jobs` routes.
//!
//! A router factory, not a server: `lattice-host` owns the listener, and it
//! binds loopback only (`gateway::ensure_loopback` refuses anything else). That
//! is the whole of the trust model for `POST /host/jobs/tick` — the caller is
//! already on this machine, and the action it can take is "do now what the
//! timer would have done in a minute". Mounting this router on a listener that
//! is not loopback-only would hand that button to the network; nothing here can
//! check that for you, so it is stated rather than assumed.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::sync::Arc;

use axum::extract::State;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};

use crate::scheduler::Scheduler;

/// `GET /host/jobs` and `POST /host/jobs/tick`, ready to merge into the host.
pub fn router(state: Arc<Scheduler>) -> Router {
    Router::new()
        .route("/host/jobs", get(jobs_status))
        .route("/host/jobs/tick", post(jobs_tick))
        .with_state(state)
}

/// `GET /host/jobs` — the schedule, the last ticks, and the backlog.
pub async fn jobs_status(State(state): State<Arc<Scheduler>>) -> Response {
    Json(state.snapshot().await).into_response()
}

/// `POST /host/jobs/tick` — run one tick now, then answer the same shape
/// `GET /host/jobs` does, so the caller reads the tick it just caused as
/// `last_tick` rather than having to learn a second payload.
pub async fn jobs_tick(State(state): State<Arc<Scheduler>>) -> Response {
    state.tick().await;
    Json(state.snapshot().await).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::SchedulerConfig;

    #[test]
    fn the_router_builds_without_route_conflicts() {
        let scheduler = Scheduler::new(SchedulerConfig::new("http://127.0.0.1:1")).expect("built");
        let _router = router(Arc::new(scheduler));
    }
}
