use std::collections::BTreeSet;
use std::sync::OnceLock;

use axum::http::Method;

pub const FIXTURE: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../fixtures/worker_allowlist.json"
));

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct WorkerRoute {
    pub method: String,
    pub path: String,
    pub axum: String,
    pub group: String,
}

impl WorkerRoute {
    fn greedy_prefix(&self) -> Option<&str> {
        self.axum.split_once('*').map(|(prefix, _)| prefix)
    }
}

#[derive(Debug, Default)]
pub struct Allowlist {
    routes: Vec<WorkerRoute>,
    literal: BTreeSet<(String, String)>,
    greedy: Vec<(String, String)>,
}

impl Allowlist {
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

    pub fn shared() -> &'static Allowlist {
        static SHARED: OnceLock<Allowlist> = OnceLock::new();
        SHARED.get_or_init(|| {
            Allowlist::parse(FIXTURE).unwrap_or_else(|err| {
                panic!("rust/fixtures/worker_allowlist.json is unusable: {err}")
            })
        })
    }

    pub fn routes(&self) -> &[WorkerRoute] {
        &self.routes
    }

    pub fn len(&self) -> usize {
        self.routes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.routes.is_empty()
    }

    pub fn allows(&self, method: &Method, path: &str) -> bool {
        let asked = method.as_str();
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
            (Method::POST, "/worker/rerank"),
            (Method::POST, "/worker/llm/stream"),
            (Method::GET, "/models"),
            (Method::POST, "/models/load"),
            (Method::POST, "/engines/prepare-model"),
        ] {
            assert!(allowlist.allows(&method, path), "{method} {path}");
        }
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
        assert!(!allowlist.allows(&Method::DELETE, "/models/unload/"));
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
