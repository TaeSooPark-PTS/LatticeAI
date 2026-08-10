#!/usr/bin/env python3
"""Verify the Lattice AI model capability registry against the Hugging Face API.

Usage:
  python3 scripts/verify_hf_model_registry.py
  python3 scripts/verify_hf_model_registry.py --out verification_report.json

What it does
------------
For every entry in the registry (recommended *and* recognised-only) it asks the
public HF REST API for the repo metadata and its file tree, then records:

* whether the repo exists and is reachable **without credentials**
* the Hub's canonical id, so a case drift in our catalog is caught
  (``mlx-community/gemma-4-12b-it-4bit`` answers as ``…-12B-it-4bit``)
* the ``gated`` flag — a gated repo cannot be downloaded by our users
* ``library_name`` / tags, the config ``model_type``, downloads, likes,
  ``lastModified``
* the sibling files: ``config.json``, at least one ``.safetensors`` shard, and a
  tokenizer file — plus the exact byte sum of every sibling, which is compared
  against the ``size`` / ``download_size_gb`` recorded in the registry.

**It never downloads weights and never loads a model.** There is deliberately no
flag that could: the only network calls are two JSON GETs per repo. Verifying a
catalog must not cost the person running it a 20GB download.

The loadability verdict — and what it is *not*
----------------------------------------------
"Can this model actually load?" is answered **statically**, from three signals:

  (a) the repo declares the MLX library (``library_name == "mlx"`` or an ``mlx``
      tag), so an MLX-format conversion exists;
  (b) the config architecture (``model_type``) is in SUPPORTED_MLX_ARCHITECTURES
      below, i.e. a loader for it shipped in mlx-lm / mlx-vlm; and
  (c) the community has downloaded it — a repo nobody has ever pulled is not
      evidence of anything.

**This is not a load test.** It cannot detect a corrupt shard, a quantisation
the installed mlx build rejects, a tokenizer mismatch, or an mlx-vlm version
older than the architecture. A ``loadable`` verdict here means "nothing in the
published metadata says this cannot load", not "this loaded". The only authority
on a real load remains the loader plus the smoke test on the user's own machine.

Exit code: 0 when every recommended entry is reachable, ungated, correctly cased
and statically loadable; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add repo root so we can import the registry directly
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

try:
    from latticeai.services.model_capability_registry import (
        RECOMMENDED,
        ModelCapability,
        get_all_capabilities,
    )
except Exception as e:  # pragma: no cover - import guard for standalone runs
    print("ERROR: Could not import model_capability_registry:", e)
    sys.exit(2)


HF_API = "https://huggingface.co/api/models/{repo}"
HF_TREE = "https://huggingface.co/api/models/{repo}/tree/main?recursive=true"

# Architectures with a loader in the MLX stack, as published by the projects
# themselves. Source (checked 2026-08-10):
#   mlx-lm   — https://github.com/ml-explore/mlx-lm/tree/main/mlx_lm/models
#   mlx-vlm  — https://github.com/Blaizzy/mlx-vlm/tree/main/mlx_vlm/models
# The module basename in those directories *is* the HF config ``model_type``,
# which is why this can be matched exactly rather than guessed. Entries here are
# limited to the architectures this registry actually ships or recognises; it is
# not a mirror of the full upstream list.
SUPPORTED_MLX_ARCHITECTURES: Dict[str, str] = {
    # vision-language loaders (mlx-vlm)
    "gemma4": "mlx-vlm",
    "gemma4_unified": "mlx-vlm",
    "gemma3": "mlx-vlm",
    "qwen3_5": "mlx-vlm",
    "qwen3_5_moe": "mlx-vlm",
    "qwen3_vl": "mlx-vlm",
    "qwen3_vl_moe": "mlx-vlm",
    "qwen2_5_vl": "mlx-vlm",
    "mllama": "mlx-vlm",
    "llama4": "mlx-vlm",
    # text loaders (mlx-lm)
    "gpt_oss": "mlx-lm",
    "lfm2": "mlx-lm",
    "llama": "mlx-lm",
    "qwen3": "mlx-lm",
}

#: Below this many all-time downloads we refuse to call an entry proven by the
#: community. It is a weak signal on purpose — it only ever *downgrades* a
#: verdict, it never promotes one.
MIN_COMMUNITY_DOWNLOADS = 100

VERDICT_LOADABLE = "loadable_static"
VERDICT_NEEDS_REVIEW = "needs_review"
VERDICT_UNAVAILABLE = "unavailable"

LIMITATIONS = [
    "Static verdict only: no weights were downloaded and no model was loaded.",
    "A 'loadable_static' verdict means the published metadata contains nothing "
    "that rules out a load — not that a load was observed.",
    "It cannot see a corrupt shard, an incompatible quantisation, a tokenizer "
    "mismatch, or an installed mlx-vlm older than the architecture.",
    "Anonymous requests only: a repo that needs credentials is reported as "
    "unavailable, because that is what it is for our users.",
    "The loader plus the on-device smoke test remain the only authority on "
    "whether a model really runs.",
]


def _http_get(url: str, timeout: float = 20.0) -> Tuple[Optional[Any], Optional[int]]:
    """GET a JSON document. Returns ``(payload, http_status)``; payload is None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "LatticeAI-model-registry-verifier/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return (json.loads(raw) if raw.strip() else {}), int(resp.status)
    except urllib.error.HTTPError as e:
        # 404 = gone. 401 = the Hub's answer for "gone or private" to an
        # anonymous client; either way our users cannot download it.
        return None, int(e.code)
    except Exception as e:
        print(f"  net error {url}: {type(e).__name__}")
        return None, None


def _mlx_signal(info: Dict[str, Any]) -> bool:
    """True when the repo advertises MLX-format weights."""
    tags = [str(t).lower() for t in (info.get("tags") or [])]
    return str(info.get("library_name") or "").lower() == "mlx" or "mlx" in tags


def _architecture(info: Dict[str, Any]) -> str:
    config = info.get("config") or {}
    return str(config.get("model_type") or "").strip().lower()


def _siblings(repo: str) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    tree, status = _http_get(HF_TREE.format(repo=repo))
    return (tree if isinstance(tree, list) else []), status


def verify_one(cap: ModelCapability) -> Dict[str, Any]:
    """Measure one registry entry against the Hub. Metadata requests only."""
    repo = cap.hf_repo_id
    result: Dict[str, Any] = {
        "id": cap.id,
        "hf_repo_id": repo,
        "lifecycle": cap.lifecycle,
        "family": cap.family,
        "modality": cap.modality,
        "registry_size": cap.size,
        "registry_architecture": cap.architecture,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "hf_exists": False,
        "http_status": None,
        "canonical_id": None,
        "canonical_case_matches": None,
        "gated": None,
        "library_name": None,
        "tags_sample": [],
        "pipeline_tag": None,
        "architecture": None,
        "architecture_matches_registry": None,
        "architecture_supported_by": None,
        "downloads": None,
        "likes": None,
        "lastModified": None,
        "has_config": False,
        "has_tokenizer": False,
        "safetensors_count": 0,
        "measured_size_gb": None,
        "registry_size_gb": cap.download_size_gb,
        "size_delta_gb": None,
        "mlx_signal": False,
        "verdict": VERDICT_UNAVAILABLE,
        "reasons": [],
        "notes": "",
    }
    reasons: List[str] = result["reasons"]

    info, status = _http_get(HF_API.format(repo=repo))
    result["http_status"] = status
    if info is None:
        reasons.append(f"repo not reachable anonymously (HTTP {status})")
        result["notes"] = "404/401 on the HF API — cannot be downloaded without credentials."
        return result

    result["hf_exists"] = True
    result["canonical_id"] = info.get("id")
    result["canonical_case_matches"] = info.get("id") == repo
    result["gated"] = info.get("gated")
    result["library_name"] = info.get("library_name")
    result["tags_sample"] = [str(t) for t in (info.get("tags") or [])][:8]
    result["pipeline_tag"] = info.get("pipeline_tag")
    result["downloads"] = info.get("downloads")
    result["likes"] = info.get("likes")
    result["lastModified"] = info.get("lastModified")

    arch = _architecture(info)
    result["architecture"] = arch or None
    result["architecture_matches_registry"] = bool(arch) and arch == cap.architecture
    result["architecture_supported_by"] = SUPPORTED_MLX_ARCHITECTURES.get(arch)
    result["mlx_signal"] = _mlx_signal(info)

    files, _tree_status = _siblings(repo)
    names = [str(f.get("path") or "") for f in files if isinstance(f, dict)]
    lowered = [n.lower() for n in names]
    result["has_config"] = "config.json" in lowered
    result["has_tokenizer"] = any("tokenizer" in n or n.endswith(".model") for n in lowered)
    result["safetensors_count"] = sum(1 for n in lowered if n.endswith(".safetensors"))
    total_bytes = sum(int(f.get("size") or 0) for f in files if isinstance(f, dict))
    if total_bytes:
        measured = round(total_bytes / 1e9, 2)
        result["measured_size_gb"] = measured
        if cap.download_size_gb is not None:
            result["size_delta_gb"] = round(measured - cap.download_size_gb, 2)

    # ── static verdict ────────────────────────────────────────────────────────
    if result["gated"]:
        reasons.append(f"gated={result['gated']} — needs Hub credentials")
    if not result["canonical_case_matches"]:
        reasons.append(f"case drift: registry {repo!r} vs canonical {result['canonical_id']!r}")
    if not result["mlx_signal"]:
        reasons.append("no mlx library_name or mlx tag")
    if result["architecture_supported_by"] is None:
        reasons.append(f"architecture {arch or '?'} is not in SUPPORTED_MLX_ARCHITECTURES")
    if not result["architecture_matches_registry"]:
        reasons.append(f"architecture {arch or '?'} != registry {cap.architecture or '?'}")
    if not result["has_config"]:
        reasons.append("no config.json in the file tree")
    if not result["has_tokenizer"]:
        reasons.append("no tokenizer file in the file tree")
    if result["safetensors_count"] < 1:
        reasons.append("no .safetensors shard in the file tree")
    downloads = result["downloads"] or 0
    if downloads < MIN_COMMUNITY_DOWNLOADS:
        reasons.append(f"only {downloads} downloads — too few to count as community-proven")
    if result["size_delta_gb"] is not None and abs(result["size_delta_gb"]) > 0.2:
        reasons.append(
            f"size drift: measured {result['measured_size_gb']}GB vs registry {cap.download_size_gb}GB"
        )

    result["verdict"] = VERDICT_LOADABLE if not reasons else VERDICT_NEEDS_REVIEW
    return result


def _print_row(r: Dict[str, Any]) -> None:
    mark = {VERDICT_LOADABLE: "OK ", VERDICT_NEEDS_REVIEW: "?? ", VERDICT_UNAVAILABLE: "XX "}[r["verdict"]]
    size = f"{r['measured_size_gb']}GB" if r["measured_size_gb"] else "-"
    print(f"{mark}{r['id']:<46} {size:>9}  {str(r['architecture'] or '-'):<16} {r['lifecycle']}")
    for reason in r["reasons"]:
        print(f"      · {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="verification_report.json", help="Report filename (written to cwd)")
    args = parser.parse_args()

    caps = get_all_capabilities()
    print("Lattice AI model registry verifier — HF metadata only, no downloads, no loads")
    print(f"Entries: {len(caps)}   Time: {datetime.now(timezone.utc).isoformat()}")
    print("-" * 96)

    results: List[Dict[str, Any]] = []
    for cap in sorted(caps, key=lambda c: (c.lifecycle != RECOMMENDED, c.display_priority, c.id)):
        result = verify_one(cap)
        _print_row(result)
        results.append(result)

    recommended = [r for r in results if r["lifecycle"] == RECOMMENDED]
    legacy = [r for r in results if r["lifecycle"] != RECOMMENDED]
    failing = [r for r in recommended if r["verdict"] != VERDICT_LOADABLE]

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "recommended_total": len(recommended),
        "legacy_total": len(legacy),
        "hf_present": sum(1 for r in results if r["hf_exists"]),
        "loadable_static": sum(1 for r in results if r["verdict"] == VERDICT_LOADABLE),
        "needs_review": sum(1 for r in results if r["verdict"] == VERDICT_NEEDS_REVIEW),
        "unavailable": sum(1 for r in results if r["verdict"] == VERDICT_UNAVAILABLE),
        "recommended_failing": [r["id"] for r in failing],
        "weights_downloaded": 0,
        "models_loaded": 0,
    }

    report = {
        "summary": summary,
        "verdict_criteria": {
            "loadable_static": [
                "repo reachable anonymously and not gated",
                "registry id matches the Hub's canonical id exactly (including case)",
                "library_name == 'mlx' or an 'mlx' tag is present",
                "config model_type is in SUPPORTED_MLX_ARCHITECTURES and matches the registry",
                "file tree has config.json, a tokenizer file and >=1 .safetensors shard",
                f"at least {MIN_COMMUNITY_DOWNLOADS} all-time downloads",
                "measured sibling byte sum is within 0.2GB of the registry's download_size_gb",
            ],
            "supported_mlx_architectures": SUPPORTED_MLX_ARCHITECTURES,
            "min_community_downloads": MIN_COMMUNITY_DOWNLOADS,
        },
        "limitations": LIMITATIONS,
        "results": results,
    }

    out_path = Path(args.out).resolve()
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("-" * 96)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\nLimitations of this verdict:")
    for line in LIMITATIONS:
        print(f"  · {line}")
    print(f"\nFull report written: {out_path}")

    if failing:
        print(f"\n**FAIL**: {len(failing)} recommended entries are not statically loadable.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
