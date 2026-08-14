"""Regression tests for /models runtime routing state."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.models import create_models_router


class _FakeRouter:
    loaded_model_ids = []
    current_model_id = None

    def detected_cloud_models(self):
        return []


def test_models_endpoint_distinguishes_gemma4_12b_runtime_update_from_26b_ready(monkeypatch):
    def fake_runtime(model_id, engine=None):
        if "gemma-4-12b" in str(model_id).lower():
            return {
                "model_id": model_id,
                "engine": engine,
                "status": "runtime_update_needed",
                "supported": False,
                "model_type": "gemma4_unified",
                "reason_code": "mlx_vlm_missing_gemma4_unified_model",
                "action": "Runtime update needed",
                "user_message": "Gemma 4 12B uses the gemma4_unified MLX format.",
            }
        return {
            "model_id": model_id,
            "engine": engine,
            "status": "supported",
            "supported": True,
            "model_type": "gemma4",
            "preferred_runtime": "MLX-VLM",
        }

    monkeypatch.setattr("latticeai.core.model_compat.model_runtime_compatibility", fake_runtime)

    catalog = {
        "local_mlx": [
            {
                "id": "mlx-community/gemma-4-12b-it-4bit",
                "name": "Gemma 4 12B Instruct",
                "family": "Gemma 4",
                "tag": "local-vlm",
                "size": "7.6GB",
                "pullable": True,
            },
            {
                "id": "mlx-community/gemma-4-26b-a4b-it-4bit",
                "name": "Gemma 4 26B A4B Instruct",
                "family": "Gemma 4",
                "tag": "local-vlm",
                "size": "15.6GB",
                "pullable": True,
            },
        ]
    }
    engines = [
        {
            "id": "local_mlx",
            "name": "MLX",
            "installed": True,
            "models": [
                {"id": "mlx-community/gemma-4-12b-it-4bit", "pulled": True},
                {"id": "mlx-community/gemma-4-26b-a4b-it-4bit", "pulled": True},
            ],
        }
    ]

    app = FastAPI()
    app.include_router(
        create_models_router(
            model_router=_FakeRouter(),
            require_user=lambda _request: "tester",
            require_admin=lambda _request: ("tester", {}),
            normalize_local_model_request=lambda model, engine=None: model,
            download_hf_model=lambda *_args, **_kwargs: {},
            prepare_and_load_model=lambda *_args, **_kwargs: {},
            prepare_and_load_model_stream=lambda *_args, **_kwargs: iter(()),
            sse_event=lambda event, data: f"event: {event}\\ndata: {data}\\n\\n",
            ensure_ollama_server=lambda: None,
            local_binary=lambda _binary: None,
            engine_status=lambda: engines,
            filter_lower_family_versions=lambda items: items,
            list_compat_profiles=lambda: [],
            engine_model_catalog=catalog,
            model_engine_aliases={
                "mlx-community/gemma-4-12b-it-4bit": {
                    "local_mlx": "mlx-community/gemma-4-12b-it-4bit",
                    "ollama": "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
                }
            },
            is_public_mode=False,
            allow_local_models=True,
            require_auth=False,
        )
    )

    data = TestClient(app).get("/models").json()
    by_id = {item["id"]: item for item in data["recommended"]}

    assert by_id["mlx-community/gemma-4-12b-it-4bit"]["load_status"] == "runtime_update_needed"
    assert by_id["mlx-community/gemma-4-12b-it-4bit"]["load_available"] is False
    assert by_id["mlx-community/gemma-4-12b-it-4bit"]["runtime_compatibility"]["model_type"] == "gemma4_unified"

    assert by_id["mlx-community/gemma-4-26b-a4b-it-4bit"]["load_status"] == "ready"
    assert by_id["mlx-community/gemma-4-26b-a4b-it-4bit"]["load_available"] is True
    assert by_id["mlx-community/gemma-4-26b-a4b-it-4bit"]["download_required"] is False
