//! `lattice-host` — opt-in front door.
//!
//! Starts the loopback gateway on the public port and supervises the Python
//! worker on an internal free port behind it. Nothing else in the product
//! changes when this binary is not running: the existing entry points (the
//! `ltcai` CLI, a browser pointed straight at the worker) keep working.

use std::net::SocketAddr;
use std::process::ExitCode;
use std::sync::Arc;

use lattice_host::gateway::{bind_loopback, mounts, serve_gateway, GatewayState};
use lattice_host::supervisor::{
    find_free_port, preferred_port, HostProbe, Supervisor, SupervisorConfig, SystemProbe,
    DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS,
};

const USAGE: &str = "\
lattice-host — Lattice AI supervisor + loopback gateway

USAGE:
    lattice-host [OPTIONS]

OPTIONS:
    --port <PORT>          Gateway port (default: LATTICEAI_HOST_PORT,
                           LATTICEAI_PORT, then 4825; scans upward if busy)
    --worker-port <PORT>   Pin the worker port instead of scanning
    --no-spawn             Do not start a worker; front an existing one
    --no-jobs              Mount /host/jobs but never run the timer
                           (same as LATTICEAI_JOBS=0)
    -h, --help             Print this help
    -V, --version          Print the version
";

#[derive(Debug, Default, PartialEq, Eq)]
struct Options {
    port: Option<u16>,
    worker_port: Option<u16>,
    no_spawn: bool,
    no_jobs: bool,
    help: bool,
    version: bool,
}

#[derive(Debug, PartialEq, Eq)]
enum ParseError {
    MissingValue(String),
    BadValue(String, String),
    Unknown(String),
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParseError::MissingValue(flag) => write!(f, "{flag} needs a value"),
            ParseError::BadValue(flag, value) => write!(f, "{flag}: '{value}' is not a port"),
            ParseError::Unknown(flag) => write!(f, "unknown option '{flag}'"),
        }
    }
}

fn parse_args<I: IntoIterator<Item = String>>(args: I) -> Result<Options, ParseError> {
    let mut options = Options::default();
    let mut iter = args.into_iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--no-spawn" => options.no_spawn = true,
            "--no-jobs" => options.no_jobs = true,
            "-h" | "--help" => options.help = true,
            "-V" | "--version" => options.version = true,
            "--port" | "--worker-port" => {
                let raw = iter
                    .next()
                    .ok_or_else(|| ParseError::MissingValue(arg.clone()))?;
                let port = raw
                    .parse::<u16>()
                    .map_err(|_| ParseError::BadValue(arg.clone(), raw.clone()))?;
                if arg == "--port" {
                    options.port = Some(port);
                } else {
                    options.worker_port = Some(port);
                }
            }
            other => return Err(ParseError::Unknown(other.to_string())),
        }
    }
    Ok(options)
}

fn log(message: &str) {
    eprintln!("lattice-host: {message}");
}

/// Gateway port: explicit flag wins, else the configured preference, and a
/// busy port is scanned past rather than fatal.
fn choose_gateway_port(options: &Options, probe: &dyn HostProbe) -> Result<u16, String> {
    let preferred = options
        .port
        .unwrap_or_else(|| preferred_port(probe, &["LATTICEAI_HOST_PORT", "LATTICEAI_PORT"]));
    find_free_port(preferred, DEFAULT_SCAN_ATTEMPTS, &[]).map_err(|err| err.to_string())
}

/// Worker port: behind the gateway on its own free port (the front-door
/// topology), unless pinned.
fn choose_worker_port(
    options: &Options,
    probe: &dyn HostProbe,
    gateway_port: u16,
) -> Result<u16, String> {
    if let Some(port) = options.worker_port {
        return Ok(port);
    }
    if options.no_spawn {
        return Ok(preferred_port(probe, &["LATTICEAI_PORT"]));
    }
    let start = gateway_port.checked_add(1).unwrap_or(DEFAULT_PORT);
    find_free_port(start, DEFAULT_SCAN_ATTEMPTS, &[gateway_port]).map_err(|err| err.to_string())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut stream) => {
                stream.recv().await;
            }
            Err(_) => std::future::pending::<()>().await,
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {}
        _ = terminate => {}
    }
    log("shutdown signal received");
}

/// Whether the jobs timer runs: the flag wins, then the environment.
///
/// A worker this process did not start is not this process's to drive, so
/// `--no-spawn` also means "no timer" — the routes still mount and still say
/// `enabled: false`, which is the honest reading of "manual only".
fn jobs_should_run(options: &Options, env_value: Option<&str>) -> bool {
    !options.no_jobs && !options.no_spawn && mounts::jobs_enabled(env_value)
}

async fn run(options: Options) -> Result<(), String> {
    let probe = SystemProbe;
    let gateway_port = choose_gateway_port(&options, &probe)?;
    let worker_port = choose_worker_port(&options, &probe, gateway_port)?;

    let mut config = SupervisorConfig::new(worker_port).with_system_dirs(&probe);
    config.supervise = !options.no_spawn;
    // The worker sits behind this gateway, so it is told to trust the gateway's
    // origin for cookie-authenticated writes (plan §2a).
    config.gateway_port = Some(gateway_port);
    let agent_root = config.agent_root();
    let supervisor = Supervisor::with_probe(config, Arc::new(SystemProbe))
        .map_err(|err| format!("cannot create the supervisor: {err}"))?;

    let addr = SocketAddr::from(([127, 0, 0, 1], gateway_port));
    let listener = bind_loopback(addr).await.map_err(|err| err.to_string())?;
    let bound = listener
        .local_addr()
        .map(|addr| addr.to_string())
        .unwrap_or_else(|_| addr.to_string());

    log(&format!("version {}", lattice_host::VERSION));
    log(&format!("gateway listening on http://{bound}"));
    log(&format!("worker origin {}", supervisor.worker_origin()));
    if options.no_spawn {
        log("--no-spawn: fronting an existing worker, nothing will be started");
    }

    let mut state = GatewayState::with_client(Arc::new(supervisor.clone()), supervisor.client());
    if let Some(root) = agent_root {
        state = state.with_agent_root(root);
    }
    // One Door: this process serves the product. Fatal on purpose — a front
    // door that came up without the platform behind it would answer 404 for
    // every product route and look like a routing bug rather than a machine
    // that cannot open its own data directory.
    let state = state
        .open_product()
        .map_err(|err| format!("cannot assemble the product surface: {err}"))?;
    log(&format!(
        "serving {} product routes natively; {} paths proxy to the worker",
        lattice_host::gateway::product::mounted_route_count(),
        lattice_host::gateway::allowlist::Allowlist::shared().len(),
    ));

    // The scheduler is built either way so `/host/jobs` always answers; only
    // the timer is conditional, and `enabled` in that payload *is* "the timer
    // is running". It is built *after* the product state because it needs that
    // state's graph writer: the drain is native now, and a scheduler without
    // the writer would keep asking the worker for a route the worker no longer
    // serves.
    let graph = state
        .product()
        .expect("open_product just succeeded")
        .graph
        .clone();
    let scheduler =
        mounts::scheduler_with_graph(&supervisor.worker_origin(), supervisor.client(), graph);
    let state = Arc::new(state.with_jobs(Arc::clone(&scheduler)));

    let run_jobs = jobs_should_run(&options, std::env::var(mounts::JOBS_ENV).ok().as_deref());
    let (jobs_stop_tx, jobs_stop_rx) = tokio::sync::oneshot::channel::<()>();
    let jobs_task = run_jobs.then(|| {
        log(&format!(
            "jobs scheduler every {}s against {} (autoresume {})",
            scheduler.config().interval.as_secs(),
            scheduler.config().worker_origin,
            if scheduler.config().autoresume {
                "on"
            } else {
                "off"
            },
        ));
        Arc::clone(&scheduler).spawn(async move {
            let _ = jobs_stop_rx.await;
        })
    });
    if !run_jobs {
        log("jobs scheduler not started; /host/jobs is manual only");
    }

    let starter = supervisor.clone();
    let start_task = tokio::spawn(async move {
        match starter.start().await {
            Ok(status) => {
                if let Some(command) = &status.command {
                    log(&format!("worker command: {command}"));
                }
                log(&format!(
                    "worker healthy on {} (pid {:?})",
                    status.origin, status.pid
                ));
            }
            Err(err) => log(&format!("worker did not come up: {err}")),
        }
    });

    let result = serve_gateway(listener, state, shutdown_signal()).await;
    let _ = start_task.await;
    let _ = jobs_stop_tx.send(());
    if let Some(handle) = jobs_task {
        let _ = tokio::time::timeout(std::time::Duration::from_secs(5), handle).await;
    }
    let final_status = supervisor.stop().await;
    log(&format!(
        "stopped (restarts: {}, last error: {})",
        final_status.restarts,
        final_status.last_error.as_deref().unwrap_or("none")
    ));
    result.map_err(|err| err.to_string())
}

#[tokio::main]
async fn main() -> ExitCode {
    let options = match parse_args(std::env::args().skip(1)) {
        Ok(options) => options,
        Err(err) => {
            eprintln!("lattice-host: {err}\n\n{USAGE}");
            return ExitCode::FAILURE;
        }
    };
    if options.help {
        println!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    if options.version {
        println!("lattice-host {}", lattice_host::VERSION);
        return ExitCode::SUCCESS;
    }
    match run(options).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            log(&err);
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_host::supervisor::StaticProbe;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn no_arguments_means_all_defaults() {
        assert_eq!(parse_args(args(&[])).expect("parse"), Options::default());
    }

    #[test]
    fn ports_and_flags_parse() {
        let options = parse_args(args(&[
            "--port",
            "5000",
            "--worker-port",
            "5001",
            "--no-spawn",
            "--no-jobs",
        ]))
        .expect("parse");
        assert_eq!(options.port, Some(5000));
        assert_eq!(options.worker_port, Some(5001));
        assert!(options.no_spawn);
        assert!(options.no_jobs);
    }

    #[test]
    fn the_jobs_timer_needs_a_worker_of_our_own_and_no_veto() {
        let default = Options::default();
        assert!(jobs_should_run(&default, None));
        assert!(jobs_should_run(&default, Some("1")));
        assert!(
            !jobs_should_run(&default, Some("0")),
            "LATTICEAI_JOBS=0 switches the timer off"
        );
        assert!(
            !jobs_should_run(
                &Options {
                    no_jobs: true,
                    ..Options::default()
                },
                Some("1")
            ),
            "--no-jobs wins over the environment"
        );
        assert!(
            !jobs_should_run(
                &Options {
                    no_spawn: true,
                    ..Options::default()
                },
                None
            ),
            "a worker we did not start is not ours to drive"
        );
    }

    #[test]
    fn help_and_version_are_recognised() {
        assert!(parse_args(args(&["-h"])).expect("parse").help);
        assert!(parse_args(args(&["--help"])).expect("parse").help);
        assert!(parse_args(args(&["-V"])).expect("parse").version);
    }

    #[test]
    fn bad_input_is_rejected_with_a_readable_message() {
        assert_eq!(
            parse_args(args(&["--port"])),
            Err(ParseError::MissingValue("--port".into()))
        );
        assert_eq!(
            parse_args(args(&["--port", "70000"])),
            Err(ParseError::BadValue("--port".into(), "70000".into()))
        );
        assert_eq!(
            parse_args(args(&["--wat"])),
            Err(ParseError::Unknown("--wat".into()))
        );
        assert!(parse_args(args(&["--port", "x"]))
            .expect_err("bad")
            .to_string()
            .contains("is not a port"));
    }

    #[test]
    fn the_worker_port_sits_behind_the_gateway_port() {
        let options = Options::default();
        let probe = StaticProbe::new();
        let worker = choose_worker_port(&options, &probe, 4825).expect("worker port");
        assert!(worker > 4825, "worker must not share the gateway port");
    }

    #[test]
    fn a_pinned_worker_port_is_used_verbatim() {
        let options = Options {
            worker_port: Some(9999),
            ..Options::default()
        };
        assert_eq!(
            choose_worker_port(&options, &StaticProbe::new(), 4825).expect("port"),
            9999
        );
    }

    #[test]
    fn no_spawn_targets_the_conventional_worker_port() {
        let options = Options {
            no_spawn: true,
            ..Options::default()
        };
        let probe = StaticProbe::new();
        assert_eq!(
            choose_worker_port(&options, &probe, 4825).expect("port"),
            DEFAULT_PORT
        );
        let pinned = StaticProbe::new().with_env("LATTICEAI_PORT", "8765");
        assert_eq!(
            choose_worker_port(&options, &pinned, 4825).expect("port"),
            8765
        );
    }

    #[test]
    fn the_gateway_port_prefers_the_flag_then_the_environment() {
        let probe = StaticProbe::new().with_env("LATTICEAI_HOST_PORT", "41100");
        let flagged = Options {
            port: Some(41200),
            ..Options::default()
        };
        let chosen = choose_gateway_port(&flagged, &probe).expect("port");
        assert!(
            (41200..41200 + DEFAULT_SCAN_ATTEMPTS).contains(&chosen),
            "flag wins, scanning upward from it (got {chosen})"
        );
        let from_env = choose_gateway_port(&Options::default(), &probe).expect("port");
        assert!(
            (41100..41100 + DEFAULT_SCAN_ATTEMPTS).contains(&from_env),
            "environment is the fallback (got {from_env})"
        );
    }

    #[test]
    fn usage_documents_every_flag() {
        for flag in [
            "--port",
            "--worker-port",
            "--no-spawn",
            "--no-jobs",
            "--help",
            "--version",
        ] {
            assert!(USAGE.contains(flag), "{flag} missing from --help");
        }
    }
}
