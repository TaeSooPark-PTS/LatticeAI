"""User profile + provider API-key helpers extracted from the app factory.

The helpers close over the user store, keyring adapter, identity migration, and
plaintext-key policy. Returning callables preserves the legacy ``server_app``
namespace while keeping ``app_factory._build`` focused on wiring.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def build_user_key_runtime(
    *,
    load_users: Any,
    save_users: Any,
    ensure_user_identity: Any,
    keyring: Any,
    allow_plaintext_api_keys: bool,
    logging: Any,
    http_exception: Any,
) -> Dict[str, Any]:
    """Return history-user and provider API-key helpers."""

    def get_history_user(email: Optional[str], nickname: Optional[str] = None) -> Dict:
        if not email:
            return {"user_email": None, "user_nickname": nickname or None}
        users = load_users()
        user = users.get(email, {})
        return {
            "user_email": email,
            "user_nickname": nickname or user.get("nickname") or user.get("name") or email,
        }

    def get_user_api_key(email: Optional[str], provider: str) -> Optional[str]:
        if not email:
            return None
        keyring_key = f"{email}:{provider}"
        if keyring is not None:
            try:
                key = keyring.get_password("LatticeAI", keyring_key)
                if key:
                    return key.strip()
            except Exception as exc:
                logging.warning("keyring read failed for %s: %s", provider, exc)
        users = load_users()
        user = users.get(email) or {}
        api_keys = user.get("api_keys") or {}
        key = api_keys.get(provider)
        if isinstance(key, str) and key.strip() and allow_plaintext_api_keys:
            return key.strip()
        return None

    def set_user_api_key(email: str, provider: str, key: str) -> None:
        keyring_key = f"{email}:{provider}"
        if keyring is not None:
            try:
                keyring.set_password("LatticeAI", keyring_key, key)
                users = load_users()
                user = users.get(email)
                if user and "api_keys" in user:
                    user["api_keys"].pop(provider, None)
                    if not user["api_keys"]:
                        user.pop("api_keys", None)
                    save_users(users)
                return
            except Exception as exc:
                logging.warning("keyring write failed for %s: %s", provider, exc)
                if not allow_plaintext_api_keys:
                    raise http_exception(
                        status_code=500,
                        detail="OS keyring에 API 키를 저장하지 못했습니다. keyring 설정을 확인하거나 LATTICEAI_ALLOW_PLAINTEXT_API_KEYS=true를 명시적으로 설정하세요.",
                    )

        if not allow_plaintext_api_keys:
            raise http_exception(
                status_code=500,
                detail="keyring 패키지를 사용할 수 없어 API 키를 안전하게 저장할 수 없습니다.",
            )

        users = load_users()
        user = users.get(email)
        if not user:
            user = {
                "password_hash": "",
                "salt": "",
                "name": email,
                "nickname": email,
                "role": "user",
                "disabled": False,
            }
        ensure_user_identity(email, user)
        api_keys = user.get("api_keys") or {}
        api_keys[provider] = key
        user["api_keys"] = api_keys
        users[email] = user
        save_users(users)

    return {
        "get_history_user": get_history_user,
        "get_user_api_key": get_user_api_key,
        "set_user_api_key": set_user_api_key,
    }


__all__ = ["build_user_key_runtime"]
