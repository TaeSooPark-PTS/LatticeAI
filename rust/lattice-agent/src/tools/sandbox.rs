use std::collections::VecDeque;
use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};

pub const MAX_FILE_BYTES: u64 = 512_000;
pub const MAX_COMMAND_SECONDS: u64 = 30;
pub const MAX_COMMAND_OUTPUT: usize = 12_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    Tool,
    Shlex,
}

impl ErrorKind {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorKind::Tool => "tool",
            ErrorKind::Shlex => "shlex",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolError {
    pub kind: ErrorKind,
    pub message: String,
}

impl ToolError {
    pub fn tool(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Tool,
            message: message.into(),
        }
    }

    pub fn shlex(message: impl Into<String>) -> Self {
        Self {
            kind: ErrorKind::Shlex,
            message: message.into(),
        }
    }
}

impl std::fmt::Display for ToolError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for ToolError {}

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
    confined: bool,
}

impl Workspace {
    pub fn new(root: impl AsRef<Path>) -> std::io::Result<Self> {
        let root = root.as_ref();
        std::fs::create_dir_all(root)?;
        Ok(Self {
            root: std::fs::canonicalize(root)?,
            confined: false,
        })
    }

    pub fn for_isolated_scope(spec: &IsolatedRootSpec) -> Result<Self, ToolError> {
        let derived = resolve_isolated_agent_root(spec)?;
        let ws = Self::new(&derived).map_err(|err| {
            ToolError::tool(format!(
                "Failed to initialize isolated agent workspace: {err}"
            ))
        })?;
        let base_canon = std::fs::canonicalize(&spec.base_root)
            .map_err(|err| ToolError::tool(format!("Invalid base agent root: {err}")))?;
        if ws.root() != base_canon && !ws.root().starts_with(&base_canon) {
            return Err(ToolError::tool(
                "Isolated agent root escapes the base agent root.",
            ));
        }
        Ok(ws.with_confined(true))
    }

    pub fn user_personal(base_root: impl AsRef<Path>, user_id: &str) -> Result<Self, ToolError> {
        Self::for_isolated_scope(&IsolatedRootSpec {
            base_root: base_root.as_ref().to_path_buf(),
            user_id: Some(user_id.to_string()),
            tenant_id: None,
            workspace_id: None,
            shared_authorized: false,
            require_user_isolation: true,
        })
    }

    pub fn with_confined(mut self, confined: bool) -> Self {
        self.confined = confined;
        self
    }

    pub fn is_confined(&self) -> bool {
        self.confined
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn resolve(&self, path: &str) -> Result<PathBuf, ToolError> {
        if path.is_empty() {
            return Ok(self.root.clone());
        }
        let candidate = resolve_soft(&self.root.join(path));
        if self.contains(&candidate) {
            Ok(candidate)
        } else {
            Err(ToolError::tool("Path escapes the agent workspace."))
        }
    }

    pub fn contains(&self, path: &Path) -> bool {
        path == self.root || path.starts_with(&self.root)
    }

    pub fn relative(&self, path: &Path) -> String {
        if path == self.root {
            return ".".into();
        }
        path.strip_prefix(&self.root)
            .map(|rest| rest.to_string_lossy().into_owned())
            .unwrap_or_else(|_| path.to_string_lossy().into_owned())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IsolatedRootSpec {
    pub base_root: PathBuf,
    pub user_id: Option<String>,
    pub tenant_id: Option<String>,
    pub workspace_id: Option<String>,
    pub shared_authorized: bool,
    pub require_user_isolation: bool,
}

pub fn sanitize_identity_segment(raw: &str) -> String {
    use sha2::{Digest, Sha256};

    let trimmed = raw.trim();
    let mut slug = String::new();
    let mut prev_dash = false;
    for ch in trimmed.chars() {
        let lower = ch.to_ascii_lowercase();
        if lower.is_ascii_alphanumeric() || lower == '_' {
            slug.push(lower);
            prev_dash = false;
        } else if !prev_dash && !slug.is_empty() {
            slug.push('-');
            prev_dash = true;
        }
        if slug.len() >= 32 {
            break;
        }
    }
    let slug = slug.trim_matches('-');
    let prefix = if slug.is_empty() || slug == "." || slug == ".." {
        "id"
    } else {
        slug
    };
    let mut hasher = Sha256::new();
    hasher.update(trimmed.as_bytes());
    let digest = hasher.finalize();
    let mut hex = String::with_capacity(12);
    for byte in &digest[..6] {
        use std::fmt::Write;
        let _ = write!(hex, "{byte:02x}");
    }
    format!("{prefix}-{hex}")
}

pub fn is_shared_workspace_id(workspace_id: Option<&str>) -> bool {
    let Some(ws) = workspace_id.map(str::trim).filter(|s| !s.is_empty()) else {
        return false;
    };
    !ws.eq_ignore_ascii_case("personal") && !ws.eq_ignore_ascii_case("default")
}

pub fn resolve_isolated_agent_root(spec: &IsolatedRootSpec) -> Result<PathBuf, ToolError> {
    if let Some(ws) = spec
        .workspace_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        if ws.contains('\0') || ws.contains('/') || ws.contains('\\') || ws.contains("..") {
            return Err(ToolError::tool("Invalid workspace identifier."));
        }
    }
    if let Some(tenant) = spec
        .tenant_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        if tenant.contains('\0')
            || tenant.contains('/')
            || tenant.contains('\\')
            || tenant.contains("..")
        {
            return Err(ToolError::tool("Invalid tenant identifier."));
        }
    }

    std::fs::create_dir_all(&spec.base_root)
        .map_err(|err| ToolError::tool(format!("Failed to prepare base agent root: {err}")))?;
    let mut scope_base = spec.base_root.clone();
    if let Some(tenant) = spec
        .tenant_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        scope_base = scope_base
            .join("tenants")
            .join(sanitize_identity_segment(tenant));
    }

    if is_shared_workspace_id(spec.workspace_id.as_deref()) {
        if !spec.shared_authorized {
            return Err(ToolError::tool(
                "Unauthorized shared workspace access denied.",
            ));
        }
        let ws = spec.workspace_id.as_deref().unwrap_or_default().trim();
        return Ok(scope_base
            .join("workspaces")
            .join(sanitize_identity_segment(ws)));
    }

    if let Some(user) = spec
        .user_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        return Ok(scope_base
            .join("users")
            .join(sanitize_identity_segment(user)));
    }

    if spec.require_user_isolation {
        return Err(ToolError::tool(
            "Authenticated user identity is required for isolated agent execution.",
        ));
    }
    Ok(spec.base_root.clone())
}

pub fn resolve_soft(path: &Path) -> PathBuf {
    #[derive(Clone)]
    enum Step {
        Root(OsString),
        Parent,
        Name(OsString),
    }

    fn steps_of(path: &Path) -> Vec<Step> {
        path.components()
            .filter_map(|component| match component {
                Component::CurDir => None,
                Component::ParentDir => Some(Step::Parent),
                Component::RootDir | Component::Prefix(_) => {
                    Some(Step::Root(component.as_os_str().to_os_string()))
                }
                Component::Normal(name) => Some(Step::Name(name.to_os_string())),
            })
            .collect()
    }

    let mut pending: VecDeque<Step> = steps_of(path).into();
    let mut out = PathBuf::new();
    let mut budget = 40;

    while let Some(step) = pending.pop_front() {
        match step {
            Step::Root(root) => {
                out.push(root);
                continue;
            }
            Step::Parent => {
                out.pop();
                continue;
            }
            Step::Name(name) => out.push(name),
        }
        if budget == 0 {
            continue;
        }
        if let Ok(target) = std::fs::read_link(&out) {
            budget -= 1;
            out.pop();
            if target.is_absolute() {
                out = PathBuf::new();
            }
            for step in steps_of(&target).into_iter().rev() {
                pending.push_front(step);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn workspace() -> (tempfile::TempDir, Workspace) {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().join("agent_workspace");
        let workspace = Workspace::new(&root).expect("workspace");
        std::fs::create_dir_all(root.join("notes")).expect("notes");
        std::fs::write(root.join("notes/a.txt"), "alpha\n").expect("file");
        std::fs::write(dir.path().join("outside_secret.txt"), "secret\n").expect("outside");
        (dir, workspace)
    }

    #[cfg(unix)]
    fn symlink(target: &str, link: &Path) {
        std::os::unix::fs::symlink(target, link).expect("symlink");
    }

    #[test]
    fn the_root_is_canonical_so_containment_is_decidable() {
        let (dir, workspace) = workspace();
        assert!(workspace.root().is_absolute());
        assert_eq!(
            workspace.root(),
            std::fs::canonicalize(dir.path().join("agent_workspace"))
                .expect("canonical")
                .as_path()
        );
    }

    #[test]
    fn ordinary_paths_resolve_inside() {
        let (_dir, workspace) = workspace();
        assert_eq!(workspace.resolve("").expect("root"), workspace.root());
        assert_eq!(workspace.resolve(".").expect("dot"), workspace.root());
        let file = workspace.resolve("notes/a.txt").expect("file");
        assert_eq!(workspace.relative(&file), "notes/a.txt");
        let missing = workspace.resolve("notes/deeper/new.txt").expect("missing");
        assert_eq!(workspace.relative(&missing), "notes/deeper/new.txt");
        assert_eq!(workspace.relative(workspace.root()), ".");
    }

    #[test]
    fn traversal_and_absolute_paths_are_refused() {
        let (_dir, workspace) = workspace();
        for path in ["..", "../outside_secret.txt", "/etc/passwd", "notes/../.."] {
            let err = workspace.resolve(path).expect_err("must refuse");
            assert_eq!(err.message, "Path escapes the agent workspace.");
            assert_eq!(err.kind, ErrorKind::Tool);
        }
    }

    #[test]
    fn an_absolute_path_inside_the_root_is_accepted() {
        let (_dir, workspace) = workspace();
        let inside = workspace.root().join("notes/a.txt");
        let resolved = workspace
            .resolve(&inside.to_string_lossy())
            .expect("absolute inside");
        assert_eq!(workspace.relative(&resolved), "notes/a.txt");
    }

    #[cfg(unix)]
    #[test]
    fn a_symlink_escape_is_refused_and_a_symlink_inside_is_not() {
        let (_dir, workspace) = workspace();
        symlink(
            "../outside_secret.txt",
            &workspace.root().join("escape_link"),
        );
        symlink("notes/a.txt", &workspace.root().join("inside_link"));
        let err = workspace.resolve("escape_link").expect_err("must refuse");
        assert_eq!(err.message, "Path escapes the agent workspace.");
        let inside = workspace.resolve("inside_link").expect("inside link");
        assert_eq!(workspace.relative(&inside), "notes/a.txt");
    }

    #[cfg(unix)]
    #[test]
    fn dotdot_is_applied_after_the_symlink_is_followed() {
        let (_dir, workspace) = workspace();
        std::fs::create_dir_all(workspace.root().join("notes/sub/deep")).expect("dirs");
        symlink("sub/deep", &workspace.root().join("notes/link"));
        assert_eq!(
            resolve_soft(&workspace.root().join("notes/link")),
            workspace.root().join("notes/sub/deep")
        );
        assert_eq!(
            resolve_soft(&workspace.root().join("notes/link/..")),
            workspace.root().join("notes/sub")
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_symlink_loop_terminates_instead_of_hanging() {
        let (_dir, workspace) = workspace();
        symlink("b", &workspace.root().join("a"));
        symlink("a", &workspace.root().join("b"));
        let resolved = resolve_soft(&workspace.root().join("a"));
        assert!(resolved.starts_with(workspace.root()));
    }

    #[test]
    fn the_error_kinds_name_themselves_for_the_goldens() {
        assert_eq!(ErrorKind::Tool.as_str(), "tool");
        assert_eq!(ErrorKind::Shlex.as_str(), "shlex");
        assert_eq!(
            ToolError::shlex("No closing quotation").to_string(),
            "No closing quotation"
        );
    }

    #[test]
    fn the_constants_are_the_python_constants() {
        assert_eq!(MAX_FILE_BYTES, 512_000);
        assert_eq!(MAX_COMMAND_SECONDS, 30);
        assert_eq!(MAX_COMMAND_OUTPUT, 12_000);
    }
}
