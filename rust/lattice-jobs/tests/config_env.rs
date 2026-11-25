use std::time::Duration;

use lattice_jobs::{SchedulerConfig, AUTORESUME_ENV, DEFAULT_INTERVAL, INTERVAL_ENV, MIN_INTERVAL};

#[test]
fn the_environment_configures_the_schedule_and_the_opt_in() {
    std::env::remove_var(INTERVAL_ENV);
    std::env::remove_var(AUTORESUME_ENV);
    let default = SchedulerConfig::from_env("http://127.0.0.1:4825");
    assert_eq!(default.interval, DEFAULT_INTERVAL);
    assert!(!default.autoresume, "autoresume must be opt-in");

    std::env::set_var(INTERVAL_ENV, "300");
    std::env::set_var(AUTORESUME_ENV, "1");
    let configured = SchedulerConfig::from_env("http://127.0.0.1:4825");
    assert_eq!(configured.interval, Duration::from_secs(300));
    assert!(configured.autoresume);

    std::env::set_var(INTERVAL_ENV, "1");
    assert_eq!(
        SchedulerConfig::from_env("http://127.0.0.1:4825").interval,
        MIN_INTERVAL
    );

    std::env::set_var(INTERVAL_ENV, "as often as possible");
    std::env::set_var(AUTORESUME_ENV, "please");
    let junk = SchedulerConfig::from_env("http://127.0.0.1:4825");
    assert_eq!(junk.interval, DEFAULT_INTERVAL);
    assert!(!junk.autoresume);

    std::env::remove_var(INTERVAL_ENV);
    std::env::remove_var(AUTORESUME_ENV);
}
