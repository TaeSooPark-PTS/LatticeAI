//! The reverse proxy's allowlist — what the Python worker still answers.
//!
//! Until v11.6.0 the gateway forwarded *everything* it did not serve itself.
//! That was the right default while the worker was the product server and the
//! host mounted a handful of `/rust/*` lanes beside it. After One Door the
//! relationship is inverted: the gateway **is** the product server, and the
//! worker is a compute box behind it — inference, embeddings, parsers,
//! renderers, ASR, and the model lifecycle that has to live in the interpreter
//! holding the weights.
//!
//! An open fall-through under that arrangement is a hole, not a default. Every
//! route the gateway forgot to mount would silently reach a Python process that
//! (after WP-P1) no longer serves it, and the browser would get the worker's
//! 404 instead of the front door's — indistinguishable from a working route
//! with a bad argument. Worse, any path at all could be aimed at the worker's
//! internal surface simply by asking for it.
//!
//! So the fall-through becomes an allowlist, and the allowlist has exactly one
//! source: [`worker_route_keys()`][keys] in
//! `latticeai/runtime/build_phases/worker_profile.py`. That set is projected
//! into `rust/fixtures/worker_allowlist.json` by
//! `scripts/gen_worker_allowlist_fixture.py`, committed, compiled into this
//! binary with [`include_str!`], and pinned from the Python side by
//! `tests/unit/test_worker_allowlist.py` — so a route added to the worker
//! without regenerating fails CI rather than 404ing in production.
//!
//! [keys]: https://github.com/TaeSooPark-PTS/lattice-ai
//!
//! ## What "matching" means here
//!
//! * **Method and path both.** `POST /models/load` is the worker's;
//!   `GET /models/load` is nobody's, and answering 404 for it is the truth.
//! * **HEAD follows GET.** A `HEAD` on a path whose `GET` is the worker's is
//!   forwarded, because the *worker* owns the answer (200 or 405) and the
//!   gateway inventing one would be this hop claiming authority it does not
//!   have. It carries no body and changes nothing.
//! * **Greedy converters are prefixes.** FastAPI's `{model_id:path}` matches
//!   slashes, so `/models/unload/mlx-community/Qwen3-8B` is one route. The
//!   fixture records the axum spelling (`/models/unload/*model_id`) and the
//!   matcher treats everything after the prefix as the parameter — refusing an
//!   empty one, because `*name` requires at least one character.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use axum::http::Method;

/// The committed projection of the worker's route set.
///
/// Compiled in rather than read from disk: a shipped binary has no `fixtures/`
/// directory next to it, and a front door whose security boundary depends on a
/// file it might not find is not a boundary.
pub const FIXTURE: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../fixtures/worker_allowlist.json"
));

/// One route the gateway may forward.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct WorkerRoute {
    /// HTTP method, uppercase, as FastAPI declares it.
    pub method: String,
    /// FastAPI's path, converters included.
    pub path: String,
    /// The same path as axum 0.7 spells it (`*name` / `:name`).
    pub axum: String,
    /// Which worker tuple it came from — `product`, `state_seam`, `compute_seam`.
    pub group: String,
}

impl WorkerRoute {
    /// The literal prefix of a greedy route, or `None` when it has no wildcard.
    fn greedy_prefix(&self) -> Option<&str> {
        self.axum.split_once('*').map(|(prefix, _)| prefix)
    }
}

/// The compiled allowlist.
#[derive(Debug, Default)]
pub struct Allowlist {
    routes: Vec<WorkerRoute>,
    literal: BTreeSet<(String, String)>,
    greedy: Vec<(String, String)>,
}

impl Allowlist {
    /// Parse the fixture. A malformed fixture is a build-time mistake, so this
    /// reports it rather than guessing an empty (or, worse, open) allowlist.
    pub fn parse(source: &str) -> Result<Self, String> {
        let document: serde_json::Value =
            serde_json::from_str(source).map_err(|err| format!("allowlist is not JSON: {err}"))?;
        let rows = document
            .get("routes")
            .and_then(|value| value.as_array())
            .ok_or_else(|| "allowlist has no `routes` array".to_string())?;
        let mut routes = Vec::with_capacity(rows.len());
        for row in rows {
            let field = |name: &str| {
                row.get(name)
                    .and_then(|value| value.as_str())
                    .map(str::to_string)
                    .ok_or_else(|| format!("allowlist row is missing `{name}`: {row}"))
            };
            routes.push(WorkerRoute {
                method: field("method")?,
                path: field("path")?,
                axum: field("axum")?,
                group: field("group")?,
            });
        }
        if routes.is_empty() {
            return Err("the allowlist is empty; the gateway would proxy nothing".into());
        }
        let mut literal = BTreeSet::new();
        let mut greedy = Vec::new();
        for route in &routes {
            match route.greedy_prefix() {
                Some(prefix) => greedy.push((route.method.clone(), prefix.to_string())),
                None => {
                    literal.insert((route.method.clone(), route.axum.clone()));
                }
            }
        }
        Ok(Self {
            routes,
            literal,
            greedy,
        })
    }

    /// An allowlist assembled by hand from `(method, axum path)` pairs.
    ///
    /// For a caller that fronts something other than the product's own worker —
    /// a test harness with its own fixture routes, or a host supervising a
    /// worker built from a different profile. The product uses
    /// [`Allowlist::shared`], which is the committed projection and the only one
    /// with a drift gate behind it.
    pub fn from_pairs<I>(rows: I) -> Self
    where
        I: IntoIterator<Item = (String, String)>,
    {
        let routes: Vec<WorkerRoute> = rows
            .into_iter()
            .map(|(method, axum)| WorkerRoute {
                method,
                path: axum.clone(),
                axum,
                group: "custom".into(),
            })
            .collect();
        let mut literal = BTreeSet::new();
        let mut greedy = Vec::new();
        for route in &routes {
            match route.greedy_prefix() {
                Some(prefix) => greedy.push((route.method.clone(), prefix.to_string())),
                None => {
                    literal.insert((route.method.clone(), route.axum.clone()));
                }
            }
        }
        Self {
            routes,
            literal,
            greedy,
        }
    }

    /// The process-wide allowlist, parsed once from [`FIXTURE`].
    ///
    /// Panics only if the compiled-in fixture is malformed, which is a broken
    /// build rather than a runtime condition — and the alternative (an empty
    /// allowlist that silently 404s the whole worker) hides it.
    pub fn shared() -> &'static Allowlist {
        static SHARED: OnceLock<Allowlist> = OnceLock::new();
        SHARED.get_or_init(|| {
            Allowlist::parse(FIXTURE).unwrap_or_else(|err| {
                panic!("rust/fixtures/worker_allowlist.json is unusable: {err}")
            })
        })
    }

    /// Every route, in fixture order.
    pub fn routes(&self) -> &[WorkerRoute] {
        &self.routes
    }

    /// How many routes the worker may answer.
    pub fn len(&self) -> usize {
        self.routes.len()
    }

    /// Whether the allowlist is empty. Never true for [`Allowlist::shared`].
    pub fn is_empty(&self) -> bool {
        self.routes.is_empty()
    }

    /// Whether this request may be forwarded to the worker.
    pub fn allows(&self, method: &Method, path: &str) -> bool {
        let asked = method.as_str();
        // HEAD is answered by whoever answers GET; see the module docs.
        let effective = if asked == Method::HEAD.as_str() {
            Method::GET.as_str()
        } else {
            asked
        };
        if self
            .literal
            .contains(&(effective.to_string(), path.to_string()))
        {
            return true;
        }
        self.greedy.iter().any(|(candidate, prefix)| {
            candidate == effective && path.len() > prefix.len() && path.starts_with(prefix.as_str())
        })
    }

    /// The routes in one group, for a test or a status report.
    pub fn group(&self, name: &str) -> Vec<&WorkerRoute> {
        self.routes
            .iter()
            .filter(|route| route.group == name)
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_compiled_fixture_parses_and_is_not_empty() {
        let allowlist = Allowlist::shared();
        assert!(!allowlist.is_empty());
        assert_eq!(allowlist.len(), allowlist.routes().len());
        // The three tuples the worker profile keeps apart, all present.
        for group in ["product", "state_seam", "compute_seam"] {
            assert!(
                !allowlist.group(group).is_empty(),
                "{group} routes vanished from the fixture"
            );
        }
    }

    #[test]
    fn the_worker_surface_is_allowed_and_nothing_else_is() {
        let allowlist = Allowlist::shared();
        for (method, path) in [
            (Method::GET, "/health"),
            (Method::POST, "/agent/llm"),
            (Method::POST, "/agent/tool"),
            (Method::POST, "/worker/embed"),
            (Method::POST, "/worker/llm/stream"),
            (Method::GET, "/models"),
            (Method::POST, "/models/load"),
            (Method::POST, "/engines/prepare-model"),
        ] {
            assert!(allowlist.allows(&method, path), "{method} {path}");
        }
        // Native families: answered here or 404, never forwarded.
        for (method, path) in [
            (Method::POST, "/chat"),
            (Method::GET, "/history"),
            (Method::GET, "/workspace/os"),
            (Method::POST, "/knowledge-graph/ingest"),
            (Method::POST, "/api/index/drain"),
            (Method::POST, "/upload/document"),
            (Method::POST, "/tools/create_docx"),
            (Method::POST, "/worker/graph/mutate"),
            (Method::GET, "/app"),
            (Method::GET, "/nonsense"),
        ] {
            assert!(!allowlist.allows(&method, path), "{method} {path}");
        }
    }

    /// v11.8.0 deleted nine caller-less worker routes. The fixture is the
    /// gateway's only source of truth for what may be forwarded, so an
    /// unregenerated fixture would keep proxying paths the worker 404s — and
    /// a request for one would come back as the *worker's* 404, which reads
    /// like a live route with a bad argument.
    #[test]
    fn the_routes_v11_8_0_deleted_are_no_longer_forwarded() {
        let allowlist = Allowlist::shared();
        for (method, path) in [
            (Method::GET, "/api/embeddings/providers"),
            (Method::GET, "/tools/pdf_pages"),
            (Method::POST, "/tools/read_document"),
            (Method::GET, "/api/ingestion/multimodal"),
            (Method::GET, "/api/capture/voice/status"),
            (Method::POST, "/models/switch/gemma-3"),
            (Method::DELETE, "/models/unload-all"),
            (Method::POST, "/engines/pull-model"),
            (Method::POST, "/worker/multimodal/describe"),
        ] {
            assert!(!allowlist.allows(&method, path), "{method} {path}");
        }
    }

    #[test]
    fn the_method_has_to_match() {
        let allowlist = Allowlist::shared();
        assert!(allowlist.allows(&Method::POST, "/models/load"));
        assert!(!allowlist.allows(&Method::GET, "/models/load"));
        assert!(!allowlist.allows(&Method::DELETE, "/health"));
        // …except HEAD, which the worker answers or refuses for itself.
        assert!(allowlist.allows(&Method::HEAD, "/health"));
        assert!(!allowlist.allows(&Method::HEAD, "/models/load"));
    }

    #[test]
    fn a_greedy_converter_matches_slashes_and_refuses_an_empty_parameter() {
        let allowlist = Allowlist::shared();
        assert!(allowlist.allows(&Method::DELETE, "/models/unload/gemma-3"));
        assert!(allowlist.allows(
            &Method::DELETE,
            "/models/unload/mlx-community/Qwen3-8B-Instruct-4bit"
        ));
        // `/*name` needs at least one character after the slash.
        assert!(!allowlist.allows(&Method::DELETE, "/models/unload/"));
        // …and the prefix is a prefix of a path, not of a name.
        assert!(!allowlist.allows(&Method::DELETE, "/models/unloaded"));
    }

    #[test]
    fn a_malformed_fixture_is_reported_rather_than_defaulted() {
        for (source, needle) in [
            ("{", "not JSON"),
            ("{}", "no `routes`"),
            (r#"{"routes": []}"#, "empty"),
            (r#"{"routes": [{"method": "GET"}]}"#, "missing `path`"),
        ] {
            let err = Allowlist::parse(source).expect_err("must refuse");
            assert!(err.contains(needle), "{source} → {err}");
        }
    }

    #[test]
    fn every_row_carries_both_spellings() {
        for route in Allowlist::shared().routes() {
            assert!(!route.method.is_empty() && route.path.starts_with('/'));
            assert_eq!(
                route.path.contains(":path}"),
                route.axum.contains('*'),
                "{} {} — greedy in one spelling only",
                route.method,
                route.path
            );
        }
    }
}
