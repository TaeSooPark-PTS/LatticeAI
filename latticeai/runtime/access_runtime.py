from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from latticeai.core.quiet import quiet


def is_externally_reachable(config: Any) -> bool:
    return bool(
        getattr(config, "is_public", False) or getattr(config, "network_exposed", False)
    )


def _extract_bearer_token(request: Any) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.cookies.get("session_token")


def _resolve_active_session_email(
    identity: Optional[str],
    users: Dict,
    normalize_email: Callable[[str], str],
) -> Optional[str]:
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
    if (
        not matched_key
        or not isinstance(user, dict)
        or bool(user.get("disabled", False))
    ):
        return None
    return normalize_email(matched_key)


def _resolve_user_role(
    email: str,
    users: Dict,
    *,
    trusted_local_owner: bool,
    admin_emails: Any,
    normalize_email: Callable[[str], str],
    normalize_role: Callable[[str], str],
) -> str:
    identity = str(email or "")
    if trusted_local_owner and not identity:
        return "owner"
    normalized_email = normalize_email(identity)
    user = (
        users.get(normalized_email)
        or users.get(identity)
        or next(
            (
                item
                for item in users.values()
                if isinstance(item, dict) and item.get("id") == identity
            ),
            {},
        )
    )
    if isinstance(user, dict) and user.get("role"):
        return normalize_role(user["role"])
    normalized_admins = {normalize_email(item) for item in admin_emails}
    if normalized_email in normalized_admins:
        return "admin"
    first_email = next(iter(users), None)
    return "admin" if first_email == normalized_email else "user"


def build_access_runtime(
    *,
    config: Any,
    require_auth: bool,
    http_exception: Any,
    request_type: Any,
    load_users: Callable[[], Dict],
    get_session_email: Callable[[str], Optional[str]],
    user_id_for_email: Callable[[Dict, Optional[str]], Optional[str]],
) -> Dict[str, Any]:

    from latticeai.core.policy import normalize_role, require_capability
    from latticeai.core.users import normalize_email

    externally_reachable = is_externally_reachable(config)
    trusted_local_owner = not require_auth and not externally_reachable

    def get_user_role(email: str, users: Optional[Dict] = None) -> str:
        return _resolve_user_role(
            email,
            users or load_users(),
            trusted_local_owner=trusted_local_owner,
            admin_emails=config.admin_emails,
            normalize_email=normalize_email,
            normalize_role=normalize_role,
        )

    def extract_bearer_token(request: request_type) -> Optional[str]:
        return _extract_bearer_token(request)

    def active_session_email(identity: Optional[str], users: Dict) -> Optional[str]:
        return _resolve_active_session_email(identity, users, normalize_email)

    def get_current_user(request: request_type) -> Optional[str]:
        token = extract_bearer_token(request)
        if token:
            try:
                return active_session_email(get_session_email(token), load_users())
            except (
                OSError,
                ValueError,
                TypeError,
                RuntimeError,
                KeyError,
                AttributeError,
            ):
                return None
        return None

    def require_user(request: request_type) -> str:
        email = get_current_user(request)
        if email:
            return email
        if trusted_local_owner:
            return ""
        raise http_exception(status_code=401, detail="인증이 필요합니다.")

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

    return {
        "get_user_role": get_user_role,
        "_extract_bearer_token": extract_bearer_token,
        "get_current_user": get_current_user,
        "require_user": require_user,
        "require_admin": require_admin,
    }
