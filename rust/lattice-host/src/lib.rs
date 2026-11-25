#![warn(clippy::all)]

pub mod gateway;
pub mod supervisor;

pub use gateway::{
    bind_loopback, build_router, serve_gateway, GatewayError, GatewayState, StatusProvider,
};
pub use supervisor::{
    csrf_trusted_origins, resolve_worker_command, BackoffPolicy, CommandOrigin, HostProbe,
    ResolveError, StaticProbe, Supervisor, SupervisorConfig, SupervisorError, SystemProbe,
    WorkerCommand, WorkerStatus,
};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
