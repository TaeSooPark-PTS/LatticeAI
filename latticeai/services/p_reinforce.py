import os
from pathlib import Path

BRAIN_DIR = Path(
    os.getenv("LATTICEAI_OBSIDIAN_VAULT_DIR")
    or os.getenv("LATTICEAI_BRAIN_DIR")
    or Path.home() / ".ltcai-brain"
)

STRUCTURE = {
    "10_Wiki": "검증된 지식, 개념 설명, 레퍼런스",
    "00_Raw": "정제되지 않은 원시 데이터, 아이디어 메모",
    "20_Skills": "재사용 가능한 코드 스니펫, 프롬프트, 워크플로",
    "30_Projects": "프로젝트별 컨텍스트, 진행 상황",
    "40_Log": "날짜별 작업 로그",
}


__all__ = ["BRAIN_DIR", "STRUCTURE"]
