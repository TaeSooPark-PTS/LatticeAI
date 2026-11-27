mod engine;
mod spec;

#[cfg(test)]
mod tests;

pub use engine::{BrainEventPayload, TriggerEngine, TriggerTickReport};
pub(crate) use spec::brain_event_config_errors;
pub use spec::{TriggerKind, MIN_INTERVAL_SECONDS};
