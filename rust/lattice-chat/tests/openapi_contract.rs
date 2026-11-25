use serde_json::Value;
use std::path::PathBuf;

fn fragment(name: &str) -> Value {
    let path: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "openapi",
        name,
    ]
    .iter()
    .collect();
    serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap()
}

fn to_openapi(path: &str) -> String {
    path.split('/')
        .map(
            |seg| match seg.strip_prefix(':').or_else(|| seg.strip_prefix('*')) {
                Some(name) => format!("{{{name}}}"),
                None => seg.to_string(),
            },
        )
        .collect::<Vec<_>>()
        .join("/")
}

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = fragment("chat.json");
    let owned: Vec<String> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|value| value.as_str().unwrap().to_string())
        .filter(|op| !op.contains(" /agent") && op != "GET /chat")
        .collect();
    let mut expected = owned;
    let mut actual: Vec<String> = lattice_chat::MOUNTED
        .iter()
        .map(|(method, path)| format!("{method} {}", to_openapi(path)))
        .collect();
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/chat.json disagree on the chat-owned split"
    );

    for (key, param) in spec["greedy_path_params"].as_object().unwrap() {
        if key.contains(" /agent") {
            continue;
        }
        let path = key.split_once(' ').unwrap().1;
        assert!(
            lattice_chat::MOUNTED.iter().any(|(_, mounted)| {
                to_openapi(mounted) == path
                    && mounted.contains(&format!("*{}", param.as_str().unwrap()))
            }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}

#[test]
fn the_agent_ops_in_the_fragment_are_not_this_crates() {
    let spec = fragment("chat.json");
    let agent: Vec<&str> = spec["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|value| value.as_str())
        .filter(|op| op.contains(" /agent"))
        .collect();
    assert!(
        !agent.is_empty(),
        "the fragment still carries /agent* for the host"
    );
    for op in agent {
        assert!(
            !lattice_chat::MOUNTED
                .iter()
                .any(|(method, path)| format!("{method} {}", to_openapi(path)) == op),
            "{op} belongs to lattice-agent/host"
        );
    }
}
