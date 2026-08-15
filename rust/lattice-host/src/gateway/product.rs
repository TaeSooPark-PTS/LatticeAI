//! Every product route family, mounted at its original path.
//!
//! This is the One Door itself. Twelve crates hand back router factories; this
//! module is the single place that decides where each one lands and what state
//! it gets, so the mount map can be read against
//! `docs/v11.6.0_ONE_DOOR_PLAN.md` in one sitting.
//!
//! | Group | Crate | Families |
//! |---|---|---|
//! | identity | `lattice-auth` | register / login / logout / account / SSO config |
//! | shell | `lattice-platform` | `static_ui`, `ui_redirects`, `/local/sysinfo` |
//! | platform | `lattice-platform` | workspace, admin, governance, mcp, tools, network, … |
//! | knowledge | `lattice-retrieval` | memory, brain, garden, chronicle, command centre, evidence, KG, search |
//! | ingest | `lattice-ingest` | local files, browser capture |
//! | jobs | `lattice-jobs` | the index API |
//! | chat | `lattice-chat` | `POST /chat` and the history lanes |
//!
//! ## Three rules that are not style
//!
//! * **Original paths.** Every client the product has — the SPA, the VS Code
//!   extension, the browser extension, the Telegram bridge — is pinned to these
//!   paths. A "cleaner" prefix would be a silent breaking change for software
//!   nobody is going to rebuild.
//! * **One owner per path.** axum panics on a duplicate route, which is the
//!   right failure: two handlers for one path means one of them is dead and
//!   nobody knows which. [`MOUNT_TABLE`] is the declared union and
//!   `tests/gateway_onedoor.rs` proves it has no duplicates *before* the router
//!   is built, so the failure is a named assertion rather than a panic in a
//!   constructor.
//! * **Page shells belong to the shell.** `GET /agents`, `GET /workspace`,
//!   `GET /graph` and the rest are 308s into the SPA's hash router. They live in
//!   [`ui_redirects`][lattice_platform::ui_redirects], not in the feature family
//!   whose Python module happens to declare them — except `GET /plugins/sdk`,
//!   which the committed contract files under `mcp_market` and `lattice-platform`'s
//!   `plugins` therefore owns (see [`REDIRECTS_OWNED_ELSEWHERE`]).

use std::sync::Arc;

use axum::Router;
use lattice_platform::static_ui::{StaticUiConfig, SysinfoState, WorkerGpuSource};
use lattice_platform::workflow_designer::LocalGraphSink;
use lattice_platform::{
    admin, agent_registry, agents, automation, change_proposals, computer_use, features,
    funnel_metrics, hooks, invitations, marketplace, mcp, models_catalog, network,
    network_boundary, permission_mode, permissions, plugins, portability, project_sessions,
    realtime, review_queue, security_dashboard, setup, static_ui, tools, ui_redirects, voice,
    workflow_designer, workspace,
};

use super::onedoor::OneDoorState;
use super::scopes::WorkspaceScopes;

/// Page redirects this crate must **not** mount, because a family router does.
///
/// `GET /plugins/sdk` is `plugins.py`'s in Python and `mcp_market.json` in the
/// committed contract, so `lattice_platform::plugins` owns it — with its own
/// `require_user`, which the redirect table does not carry. Mounting both would
/// panic; mounting the redirect instead would move an operation out of the
/// fragment its crate's contract test pins.
pub const REDIRECTS_OWNED_ELSEWHERE: [&str; 1] = ["/plugins/sdk"];

/// The declared mount map: `(family, routes)`, one row per router factory.
///
/// axum cannot be asked what it mounted (WP-I5), so the crates declare their
/// tables and this is their union. It is the thing the collision test reads,
/// the thing the integration test walks, and the number the release note
/// quotes.
pub fn mount_table() -> Vec<(&'static str, &'static [(&'static str, &'static str)])> {
    vec![
        ("platform::admin", admin::MOUNTED),
        ("platform::agent_registry", agent_registry::MOUNTED),
        ("platform::agents", agents::MOUNTED),
        ("platform::agents(loop)", agents::AGENT_LOOP_MOUNTED),
        ("platform::automation", automation::MOUNTED),
        ("platform::change_proposals", change_proposals::MOUNTED),
        ("platform::computer_use", computer_use::MOUNTED),
        ("platform::features", features::MOUNTED),
        ("platform::funnel_metrics", funnel_metrics::MOUNTED),
        ("platform::hooks", hooks::MOUNTED),
        ("platform::invitations", invitations::MOUNTED),
        ("platform::marketplace", marketplace::MOUNTED),
        ("platform::mcp", mcp::MOUNTED),
        ("platform::models_catalog", models_catalog::MOUNTED),
        ("platform::network", network::MOUNTED),
        ("platform::network_boundary", network_boundary::MOUNTED),
        ("platform::permission_mode", permission_mode::MOUNTED),
        ("platform::permissions", permissions::MOUNTED),
        ("platform::plugins", plugins::MOUNTED),
        ("platform::portability", portability::MOUNTED),
        ("platform::project_sessions", project_sessions::MOUNTED),
        ("platform::realtime", realtime::MOUNTED),
        ("platform::review_queue", review_queue::MOUNTED),
        ("platform::security_dashboard", security_dashboard::MOUNTED),
        ("platform::setup", setup::MOUNTED),
        ("platform::tools", tools::MOUNTED),
        ("platform::voice", voice::MOUNTED),
        ("platform::workflow_designer", workflow_designer::MOUNTED),
        ("platform::workspace", workspace::MOUNTED),
        (
            "retrieval::knowledge_graph",
            lattice_retrieval::knowledge_graph_api::MOUNTED,
        ),
        ("retrieval::search", lattice_retrieval::search_api::MOUNTED),
        (
            "retrieval::brain",
            lattice_retrieval::brain_api::routes::MOUNTED,
        ),
        (
            "retrieval::chronicle",
            lattice_retrieval::chronicle_api::routes::MOUNTED,
        ),
        (
            "retrieval::command_center",
            lattice_retrieval::command_center_api::routes::MOUNTED,
        ),
        (
            "retrieval::evidence",
            lattice_retrieval::evidence_api::routes::MOUNTED,
        ),
        (
            "retrieval::garden",
            lattice_retrieval::garden_api::process::MOUNTED,
        ),
        (
            "retrieval::memory",
            lattice_retrieval::memory_api::routes::MOUNTED,
        ),
        ("ingest::browser", lattice_ingest::browser_api::MOUNTED),
        (
            "ingest::local_files",
            lattice_ingest::local_files_api::MOUNTED,
        ),
        ("jobs::index", lattice_jobs::index_api::MOUNTED),
        ("chat", lattice_chat::MOUNTED),
    ]
}

/// How many product operations the gateway serves natively.
pub fn mounted_route_count() -> usize {
    mount_table().iter().map(|(_, rows)| rows.len()).sum()
}

/// The identity routes, plus the SPA shell, assets and page redirects.
///
/// The gated half — the eight `require_user` redirects and `/local/sysinfo` —
/// is layered here rather than merged into the open half, because those two
/// sets differ by exactly one thing and it is the auth gate.
pub fn shell_router(state: &OneDoorState) -> Router {
    let mut config = StaticUiConfig::from_env(state.static_dir.clone());
    config.secure_cookies = state.auth.config().secure_cookies;

    let gated = ui_redirects::router_from(authenticated_redirects())
        .merge(static_ui::sysinfo_router(Arc::new(SysinfoState {
            gpu: Arc::new(WorkerGpuSource::with_client(state.seam.clone())),
        })))
        .layer(axum::middleware::from_fn_with_state(
            Arc::clone(&state.auth),
            super::identity::require_user_layer,
        ));

    lattice_auth::router(Arc::clone(&state.auth))
        .merge(static_ui::router(config))
        .merge(ui_redirects::public_router())
        .merge(gated)
}

/// The `require_user` page redirects this crate mounts.
///
/// Filtered rather than taken whole so a path another family owns
/// ([`REDIRECTS_OWNED_ELSEWHERE`]) is left to it. The slice is leaked once at
/// first use: `ui_redirects::router_from` wants `&'static [UiRedirect]` because
/// the paths become route literals, and one small allocation for the life of
/// the process is the honest cost of not copying the table into this crate.
fn authenticated_redirects() -> &'static [ui_redirects::UiRedirect] {
    static FILTERED: std::sync::OnceLock<&'static [ui_redirects::UiRedirect]> =
        std::sync::OnceLock::new();
    FILTERED.get_or_init(|| {
        let rows: Vec<ui_redirects::UiRedirect> = ui_redirects::REDIRECTS
            .iter()
            .filter(|route| route.requires_user)
            .filter(|route| !REDIRECTS_OWNED_ELSEWHERE.contains(&route.path))
            .cloned()
            .collect();
        Box::leak(rows.into_boxed_slice())
    })
}

/// Everything `lattice-platform` serves that is not the shell.
pub fn platform_router(state: &OneDoorState) -> Router {
    let auth = || Arc::clone(&state.auth);
    let data_dir = state.data_dir.clone();

    let mut admin_state = admin::AdminState::new(auth(), &data_dir);
    admin_state.graph_stats = state.graph_stats();

    let mut security = security_dashboard::SecurityState::new(auth(), &data_dir);
    security.worker = Some(state.seam.clone());

    let mut setup_state = setup::SetupState::new(auth(), &data_dir);
    setup_state.graph = Some(state.graph.clone());
    setup_state.pipeline_available = true;

    let mut network_state = network::NetworkState::new(auth(), state.config.clone());
    network_state.graph = Some(state.graph.clone());

    let mut boundary = network_boundary::NetworkBoundaryState::new(auth(), state.config.clone());
    boundary.graph = Some(state.graph.clone());

    let mut portability_state = portability::PortabilityState::new(auth(), state.config.clone());
    portability_state.graph = Some(state.graph.clone());

    // One handle, opened in `OneDoorState`, because the agent loop stages into
    // the same document (§P1c). Both this router and the loop mutate
    // `workspace_os.json`, and `GovernanceState` is a facade over the single
    // `WorkspaceOsStore` handle: a second `GovernanceState::open` here would
    // take a different lock over the same file.
    let governance = state.governance.clone();

    Router::new()
        .merge(workspace::router(state.workspace_state.clone()))
        .merge(invitations::router(
            invitations::InvitationsState::from_workspace(&state.workspace_state),
        ))
        .merge(permissions::router(permissions::PermissionsState::new(
            auth(),
            &data_dir,
        )))
        .merge(admin::router(admin_state))
        .merge(security_dashboard::router(security))
        .merge(features::router(features::FeaturesState::new(
            auth(),
            &data_dir,
        )))
        .merge(funnel_metrics::router(funnel_metrics::FunnelState::new(
            auth(),
            &data_dir,
        )))
        .merge(setup::router(setup_state))
        .merge(review_queue::router(governance.clone()))
        .merge(change_proposals::router(governance.clone()))
        .merge(automation::router(governance.clone()))
        // The registry the agent loop's hook sink fires through, opened once in
        // `OneDoorState` — see the field's comment for why a second one loses
        // records rather than duplicating them.
        .merge(hooks::router(hooks::HooksState::with_store(
            governance,
            state.hooks.clone(),
        )))
        .merge(mcp::router(mcp::McpState::new(auth(), &data_dir)))
        .merge(marketplace::router(marketplace::MarketplaceState::new(
            auth(),
            &data_dir,
        )))
        .merge(plugins::router(plugins::PluginsState::new(
            auth(),
            &data_dir,
        )))
        .merge(agent_registry::router(
            agent_registry::AgentRegistryState::new(auth(), &data_dir),
        ))
        .merge(agents::router(agents::AgentsState::new(auth(), &data_dir)))
        .merge(tools::router(
            tools::ToolsState::new(auth(), state.workspace.clone(), &state.brain_dir)
                .with_worker(state.seam.clone()),
        ))
        .merge(portability::router(portability_state))
        .merge(network::router(network_state))
        .merge(network_boundary::router(boundary))
        .merge(realtime::router(realtime::RealtimeState::new(auth())))
        .merge(project_sessions::router(
            project_sessions::ProjectSessionsState::new(auth(), state.config.clone()),
        ))
        .merge(voice::router_with(voice::VoiceState {
            auth: Some(auth()),
            graph: Some(state.graph.clone()),
            seam: Some(state.seam.clone()),
        }))
        .merge(computer_use::router(computer_use::ComputerUseState::new(
            auth(),
            Some(state.seam.clone()),
            state.agent_root.clone(),
        )))
        .merge(models_catalog::router(
            models_catalog::ModelsCatalogState::new(auth(), &data_dir),
        ))
        .merge(permission_mode::router(
            permission_mode::PermissionModeState::new(auth(), &data_dir),
        ))
        .merge(workflow_designer::router(
            workflow_designer::WorkflowDesignerState::new(auth(), &data_dir)
                .with_graph(Arc::new(LocalGraphSink)),
        ))
}

/// Retrieval, ingest, the index API and chat — everything that reads or writes
/// the Brain.
pub fn knowledge_router(state: &OneDoorState) -> Router {
    let auth = || Arc::clone(&state.auth);
    let store = Some(Arc::clone(&state.store));

    let brain = lattice_retrieval::memory_api::BrainState::new(
        auth(),
        state.config.clone(),
        Arc::clone(&state.store),
    )
    .with_seam(state.seam.clone())
    .with_graph(state.graph.clone())
    .with_brain_dir(state.brain_dir.clone());

    let retrieval = Arc::new(
        lattice_retrieval::search_api::RetrievalApiState::new(
            auth(),
            store.clone(),
            state.config.clone(),
        )
        .with_graph(state.graph.clone())
        .with_seam(state.seam.clone())
        .with_scopes(Arc::new(WorkspaceScopes::new(Arc::clone(&state.resolver)))),
    );

    let local_files = Arc::new(
        lattice_ingest::local_files_api::LocalFilesState::new(
            auth(),
            store.clone(),
            state.config.clone(),
        )
        .with_graph(state.graph.clone())
        .with_seam(state.seam.clone())
        .with_permissions(lattice_ingest::local_files_api::LocalApprovals::new()),
    );

    let index = Arc::new(
        lattice_jobs::index_api::IndexApiState::new(auth(), store, state.config.clone())
            .with_graph(state.graph.clone())
            .with_seam(state.seam.clone()),
    );

    let chat = lattice_chat::ChatState::new(
        auth(),
        lattice_chat::ChatConfig::from_data_dir(&state.data_dir, &state.agent_root),
    )
    .with_worker(lattice_chat::ChatWorker::with_client(
        state.client.clone(),
        state.seam.origin(),
    ))
    .with_graph(state.graph.clone())
    .with_workspace(Arc::clone(&state.resolver) as Arc<dyn lattice_auth::WorkspaceResolver>);

    Router::new()
        .merge(lattice_retrieval::memory_api::router(brain.clone()))
        .merge(lattice_retrieval::brain_api::router(brain.clone()))
        .merge(lattice_retrieval::garden_api::router(brain.clone()))
        .merge(lattice_retrieval::chronicle_api::router(brain.clone()))
        .merge(lattice_retrieval::command_center_api::router(brain.clone()))
        .merge(lattice_retrieval::evidence_api::router(brain))
        .merge(lattice_retrieval::knowledge_graph_api::router(Arc::clone(
            &retrieval,
        )))
        .merge(lattice_retrieval::search_api::router(retrieval))
        .merge(lattice_ingest::local_files_api::router(local_files))
        .merge(lattice_ingest::browser_api::router(Arc::new(
            lattice_ingest::browser_api::BrowserState::new(auth(), state.config.clone())
                .with_seam(state.seam.clone()),
        )))
        .merge(lattice_jobs::index_api::router(index))
        .merge(lattice_chat::router(chat))
}

/// The whole product surface: shell, platform, knowledge.
pub fn product_router(state: &OneDoorState) -> Router {
    shell_router(state)
        .merge(platform_router(state))
        .merge(knowledge_router(state))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    #[test]
    fn no_two_families_claim_the_same_operation() {
        let mut owners: BTreeMap<(&str, &str), Vec<&str>> = BTreeMap::new();
        for (family, rows) in mount_table() {
            for (method, path) in rows {
                owners.entry((method, path)).or_default().push(family);
            }
        }
        let clashes: Vec<String> = owners
            .iter()
            .filter(|(_, families)| families.len() > 1)
            .map(|((method, path), families)| format!("{method} {path} → {families:?}"))
            .collect();
        assert!(
            clashes.is_empty(),
            "axum panics on a duplicate route, and the panic names one path at \
             a time; these are all of them: {clashes:?}"
        );
        assert_eq!(owners.len(), mounted_route_count());
    }

    #[test]
    fn the_page_redirects_this_crate_mounts_leave_the_owned_ones_alone() {
        let mounted: Vec<&str> = authenticated_redirects()
            .iter()
            .map(|route| route.path)
            .collect();
        for owned in REDIRECTS_OWNED_ELSEWHERE {
            assert!(
                !mounted.contains(&owned),
                "{owned} is another family's route; mounting it here panics"
            );
        }
        for expected in [
            "/workspace",
            "/onboarding",
            "/graph",
            "/knowledge-graph",
            "/agents",
            "/workflows",
            "/activity",
        ] {
            assert!(mounted.contains(&expected), "{expected} lost its redirect");
        }
        assert_eq!(mounted.len(), 7);
    }

    #[test]
    fn the_mount_table_is_the_product_surface_and_not_a_sample() {
        // Sanity floor: the plan moves ~440 operations across the door, and a
        // table that quietly lost a crate would still "pass" a smoke test.
        assert!(
            mounted_route_count() > 400,
            "only {} operations declared",
            mounted_route_count()
        );
        let families = mount_table().len();
        assert!(families >= 40, "only {families} families declared");
    }

    #[test]
    fn every_declared_path_is_absolute_and_uses_axum_parameter_syntax() {
        for (family, rows) in mount_table() {
            for (method, path) in rows {
                assert!(path.starts_with('/'), "{family}: {method} {path}");
                assert!(
                    !path.contains('{'),
                    "{family}: {method} {path} is spelled the way OpenAPI spells \
                     it; axum wants :name or /*name"
                );
            }
        }
    }
}
