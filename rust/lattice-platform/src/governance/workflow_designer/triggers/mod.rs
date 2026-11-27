mod engine;
mod spec;

#[cfg(test)]
mod tests;

pub use engine::{BrainEventPayload, TriggerEngine, TriggerTickReport};
pub use spec::{TriggerKind, MIN_INTERVAL_SECONDS};
