use axum::response::Response;

use super::{literal_error, optional, FieldSpec, Kind, Model, Query};
use crate::temporal::{SortBy, TemporalRanking};

pub(crate) const SORT_BY_EXPECTED: &str = "'relevance', 'newest' or 'auto'";

pub(crate) const fn sort_by_field() -> FieldSpec {
    optional("sort_by", Kind::OptStr)
}

pub(crate) const fn include_historical_field() -> FieldSpec {
    optional("include_historical", Kind::Bool)
}

pub(crate) const fn since_field() -> FieldSpec {
    optional("since", Kind::OptStr)
}

pub(crate) const fn until_field() -> FieldSpec {
    optional("until", Kind::OptStr)
}

fn sort_by(source: &str, raw: Option<&str>) -> Result<SortBy, Response> {
    SortBy::parse(raw)
        .map_err(|_| literal_error(source, "sort_by", SORT_BY_EXPECTED, raw.unwrap_or_default()))
}

pub(crate) fn ranking_from_model(model: &Model) -> Result<TemporalRanking, Response> {
    Ok(TemporalRanking::from_env().with_request(
        sort_by("body", model.opt_str("sort_by"))?,
        model.bool("include_historical", false),
        model.opt_str("since").map(str::to_string),
        model.opt_str("until").map(str::to_string),
    ))
}

pub(crate) fn ranking_from_query(query: &Query) -> Result<TemporalRanking, Response> {
    Ok(TemporalRanking::from_env().with_request(
        sort_by("query", query.raw("sort_by"))?,
        query.bool_or("include_historical", false)?,
        query.raw("since").map(str::to_string),
        query.raw("until").map(str::to_string),
    ))
}
