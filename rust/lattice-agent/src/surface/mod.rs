pub use crate::seam::worker;
pub mod looproutes;
pub mod router;
pub mod runbody;

pub(crate) fn bad_request(detail: impl Into<String>) -> axum::response::Response {
    use axum::response::IntoResponse;
    (
        axum::http::StatusCode::BAD_REQUEST,
        axum::Json(serde_json::json!({
            "error": "invalid_request",
            "detail": detail.into(),
        })),
    )
        .into_response()
}
