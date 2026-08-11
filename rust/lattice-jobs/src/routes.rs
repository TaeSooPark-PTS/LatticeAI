//! The two `/host/jobs` routes.
//!
//! A router factory, not a server: `lattice-host` owns the listener, and it
//! binds loopback only (`gateway::ensure_loopback` refuses anything else). That
//! is the whole of the trust model for `POST /host/jobs/tick` — the caller is
//! already on this machine, and the action it can take is "do now what the
//! timer would have done in a minute". Mounting this router on a listener that
//! is not loopback-only would hand that button to the network; nothing here can
//! check that for you, so it is stated rather than assumed.

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
