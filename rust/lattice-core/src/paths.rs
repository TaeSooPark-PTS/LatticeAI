use std::path::{Path, PathBuf};

pub const DATA_DIR_ENV: &str = "LATTICEAI_DATA_DIR";
pub const DEFAULT_DATA_DIR_NAME: &str = ".ltcai";
pub const DB_FILE_NAME: &str = "knowledge_graph.sqlite";

pub fn resolve_data_dir(env_value: Option<&str>, home: Option<&Path>) -> PathBuf {
    let configured = env_value.map(str::trim).unwrap_or("");
    if !configured.is_empty() {
        return PathBuf::from(configured);
    }
    match home {
        Some(dir) => dir.join(DEFAULT_DATA_DIR_NAME),
        None => PathBuf::from(DEFAULT_DATA_DIR_NAME),
    }
}

pub fn data_dir() -> PathBuf {
    let env_value = std::env::var(DATA_DIR_ENV).ok();
    let home = home_dir();
    resolve_data_dir(env_value.as_deref(), home.as_deref())
}

pub fn graph_db_path() -> PathBuf {
    data_dir().join(DB_FILE_NAME)
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .filter(|p| !p.as_os_str().is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_wins_when_set() {
        let got = resolve_data_dir(Some("/tmp/brain"), Some(Path::new("/Users/x")));
        assert_eq!(got, PathBuf::from("/tmp/brain"));
    }

    #[test]
    fn blank_env_falls_back_to_home() {
        for blank in [None, Some(""), Some("   ")] {
            let got = resolve_data_dir(blank, Some(Path::new("/Users/x")));
            assert_eq!(got, PathBuf::from("/Users/x/.ltcai"));
        }
    }

    #[test]
    fn no_home_yields_the_bare_name() {
        assert_eq!(resolve_data_dir(None, None), PathBuf::from(".ltcai"));
    }

    #[test]
    fn db_file_hangs_off_the_data_dir() {
        assert_eq!(DB_FILE_NAME, "knowledge_graph.sqlite");
        assert!(graph_db_path().ends_with(DB_FILE_NAME));
        assert!(data_dir().is_absolute() || data_dir() == Path::new(".ltcai"));
    }
}
