"""VPC network-profile config seam extracted from the legacy app factory.

``DEFAULT_VPC_CONFIG`` and the load/save helpers used to live inline in
``app_factory._build``. Behaviour and exported names are preserved exactly for
the legacy ``server_app`` compatibility namespace; the only change is that they
now live behind a builder so the factory stays a wiring path.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def build_vpc_runtime(*, vpc_file: Path, logging: Any) -> Dict[str, Any]:
    """Return ``DEFAULT_VPC_CONFIG`` and the VPC load/save helpers."""

    default_vpc_config: Dict[str, Any] = {
        "provider": "AWS",
        "region": "ap-northeast-2",
        "cidr_block": "10.42.0.0/16",
        "private_subnets": ["10.42.10.0/24", "10.42.20.0/24"],
        "endpoint": "ltcai-private.local",
        "vpn_status": "standby",
        "peering_status": "not_configured",
        "notes": "로컬 MLX 브릿지를 프라이빗 서브넷 또는 VPN 뒤에서 운영할 때 쓰는 네트워크 프로필입니다.",
        "updated_at": None,
    }

    def load_vpc_config() -> Dict:
        if not os.path.exists(vpc_file):
            return default_vpc_config.copy()
        try:
            with open(vpc_file, "r", encoding="utf-8") as f:
                stored = json.load(f)
            return {**default_vpc_config, **stored}
        except Exception as e:
            logging.warning("load_vpc_config failed (using defaults): %s", e)
            return default_vpc_config.copy()

    def save_vpc_config(config: Dict) -> None:
        config["updated_at"] = datetime.now().isoformat()
        with open(vpc_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    return {
        "DEFAULT_VPC_CONFIG": default_vpc_config,
        "load_vpc_config": load_vpc_config,
        "save_vpc_config": save_vpc_config,
    }


__all__ = ["build_vpc_runtime"]
