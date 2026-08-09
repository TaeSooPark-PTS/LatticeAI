"""Access-control helper closures for the app factory."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from latticeai.core.quiet import quiet


def build_access_runtime(
    *,
    config: Any,
    require_auth: bool,
    http_exception: Any,
    request_type: Any,
    load_users: Callable[[], Dict],
    get_session_email: Callable[[str], Optional[str]],
    # Optional in and out: an anonymous request has no email, and an email
    # with no account has no id.
    user_id_for_email: Callable[[Dict, Optional[str]], Optional[str]],
) -> Dict[str, Any]:
    """Build user/admin access helpers without changing legacy call signatures."""

    from latticeai.core.policy import normalize_role, require_capability
    from latticeai.core.users import normalize_email

    # The historical loopback/no-auth profile represents its single human as
    # an empty identity.  Keep that storage/workspace compatibility contract,
    # but project the identity as an owner at authorization boundaries.  Never
    # extend this trust to public or non-loopback bindings, even if an invalid
    # caller constructs this runtime with ``require_auth=False`` directly.
    externally_reachable = bool(
        getattr(config, "is_public", False)
        or getattr(config, "network_exposed", False)
    )
    trusted_local_owner = not require_auth and not externally_reachable
    effective_require_auth = bool(require_auth or externally_reachable)

    def get_user_role(email: str, users: Optional[Dict] = None) -> str:
        users = users or load_users()
        identity = str(email or "")
        if trusted_local_owner and not identity:
            return "owner"
        normalized_email = normalize_email(identity)
        user = users.get(normalized_email) or users.get(identity) or next(
            (
                item
                for item in users.values()
                if isinstance(item, dict) and item.get("id") == identity
            ),
            {},
        )
        if isinstance(user, dict) and user.get("role"):
            return normalize_role(user["role"])
        admin_emails = {normalize_email(item) for item in config.admin_emails}
        if normalized_email in admin_emails:
            return "admin"
        first_email = next(iter(users), None)
        return "admin" if first_email == normalized_email else "user"

    def extract_bearer_token(request: request_type) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return request.cookies.get("session_token")

    def active_session_email(identity: Optional[str], users: Dict) -> Optional[str]:
        """Resolve a session identity only while its account remains active."""
        if not identity:
            return None
        raw_identity = str(identity)
        normalized_email = normalize_email(raw_identity)
        matched_key: Optional[str] = None
        user = users.get(normalized_email)
        if isinstance(user, dict):
            matched_key = normalized_email
        else:
            user = users.get(raw_identity)
            if isinstance(user, dict):
                matched_key = raw_identity
            else:
                for key, item in users.items():
                    if isinstance(item, dict) and item.get("id") == raw_identity:
                        matched_key = str(key)
                        user = item
                        break
        # A session for a deleted account, malformed account record, or an
        # explicitly disabled account is invalid immediately. This check is
        # intentionally performed on every request so stale bearer/cookie
        # tokens cannot retain user or administrator access.
        if not matched_key or not isinstance(user, dict) or bool(user.get("disabled", False)):
            return None
        return normalize_email(matched_key)

    def get_current_user(request: request_type) -> Optional[str]:
        token = extract_bearer_token(request)
        if token:
            try:
                return active_session_email(get_session_email(token), load_users())
            except Exception:
                # Account-store failures must fail closed rather than turning a
                # stale session into an authenticated identity.
                return None
        return None

    def require_user(request: request_type) -> str:
        email = get_current_user(request)
        if email:
            # Optional authentication remains meaningful in local mode: a
            # valid session keeps its real account identity instead of being
            # collapsed into the anonymous Local User fallback.
            return email
        if trusted_local_owner:
            # A no-auth loopback caller is the trusted, anonymous local owner.
            # Returning the legacy empty identity keeps ownerless workspaces,
            # shared local vaults, and Local User profile behavior compatible;
            # get_user_role() supplies the explicit owner authorization role.
            return ""
        if effective_require_auth and not email:
            raise http_exception(status_code=401, detail="인증이 필요합니다.")
        return email or ""  # pragma: no cover — unreachable: trusted_local_owner is the exact complement of effective_require_auth

    def require_admin(request: request_type) -> tuple[str, Dict]:
        users = load_users()
        if trusted_local_owner:
            return "", users
        token = extract_bearer_token(request)
        if token:
            email = active_session_email(get_session_email(token), users)
            if email:
                role = get_user_role(email, users)
                try:
                    require_capability(role, "admin:users")
                    return email, users
                except PermissionError:
                    quiet()
        raise http_exception(status_code=403, detail="관리자 권한이 필요합니다.")

    def public_user(email: str, user: Dict, users: Dict) -> Dict:
        role = get_user_role(email, users)
        user_id = user.get("id") or user_id_for_email(users, email)
        return {
            "id": user_id,
            "email": email,
            "identity": user_id,
            "name": user.get("name", ""),
            "nickname": user.get("nickname", ""),
            "role": role,
            "disabled": bool(user.get("disabled", False)),
        }

    return {
        "get_user_role": get_user_role,
        "_extract_bearer_token": extract_bearer_token,
        "get_current_user": get_current_user,
        "require_user": require_user,
        "require_admin": require_admin,
        "public_user": public_user,
    }
