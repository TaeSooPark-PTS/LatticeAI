"""What a model *is*: where it runs, who made it, and how a ref is spelled.

``parse_model_ref`` is the single place a model id becomes ``(provider,
model)`` — everything downstream branches on ``provider == "local_mlx"``.
``source_metadata_for_model`` is the plain-Korean provenance block the model
picker shows, so "이 모델은 어디서 실행되나" has one answer per model rather
than one per surface.
"""

from dataclasses import dataclass
from typing import Any, Dict

from latticeai.models.model_providers import (
    MODEL_SOURCE_BY_FAMILY,
    OPENAI_COMPATIBLE_PROVIDERS,
)


# Returns a display payload whose `source_display_order` value is a list,
# so the value type is Any rather than str.
def source_metadata_for_model(
    provider: str, model: Dict[str, Any], *, local_server: bool
) -> Dict[str, Any]:
    family = str(model.get("family") or "")
    country, company = MODEL_SOURCE_BY_FAMILY.get(family, ("미상", provider.title()))
    if local_server:
        execution_method = "내 컴퓨터에서만 실행"
        internet_requirement = "모델을 다운로드할 때만 인터넷 필요; 실행 중에는 필요 없음"
    else:
        execution_method = "인터넷 연결 후 사용"
        internet_requirement = "내 파일이 인터넷으로 전송될 수 있음"
    return {
        "source_country": country,
        "source_company": company,
        "execution_method": execution_method,
        "internet_requirement": internet_requirement,
        "model_name": model.get("name") or model.get("id") or "",
        "source_display_order": [
            "source_country",
            "source_company",
            "execution_method",
            "internet_requirement",
            "model_name",
        ],
    }


@dataclass
class CloudModel:
    provider: str
    model: str
    client: Any  # AsyncOpenAI when the optional dependency is installed
    cache_key: str


def parse_model_ref(model_id: str) -> tuple[str, str]:
    """Return (provider, model). Unprefixed refs stay local MLX."""
    if model_id.startswith("cloud:"):
        _, provider, model = model_id.split(":", 2)
        return provider, model
    if ":" in model_id:
        provider, model = model_id.split(":", 1)
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return provider, model
        if provider in {"local_mlx", "mlx"}:
            return "local_mlx", model
    if model_id.startswith("local_mlx:"):
        return "local_mlx", model_id.split(":", 1)[1]  # pragma: no cover — dead: a "local_mlx:" ref always has a ":" and returned above
    return "local_mlx", model_id
