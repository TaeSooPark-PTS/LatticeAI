"""model_runtime trust gate unit tests for v6.1 hardening."""

from fastapi import HTTPException

from latticeai.services.model_runtime import _download_allowed, _download_block


def test_download_blocked_without_model_download_consent():
    """External download must be blocked when no explicit consent is given."""
    # Default state: no consent
    assert _download_allowed(allow_download=False) is False
    assert _download_allowed(allow_download=True) is True

    # Without consent, calling block raises the expected 409 gate
    try:
        _download_block("huggingface", "some/model")
    except HTTPException as exc:
        assert exc.status_code == 409
        detail = exc.detail
        assert isinstance(detail, dict)
        assert detail.get("capability") == "model_download"
        assert "does not start outbound model downloads by default" in detail.get("reason", "")
    else:
        assert False, "_download_block must raise when consent is absent"
