# Contributing to Lattice AI

## 개발 환경 설정

```bash
git clone https://github.com/TaeSooPark-PTS/LatticeAI.git
cd LatticeAI

# Python 환경 (Python 3.11+ 필요)
python3 -m venv venv
source venv/bin/activate
pip install -e ".[all]"
pip install pytest pytest-asyncio

# 서버 실행 (개발 모드 — 코드 변경 시 자동 재시작)
python ltcai_cli.py --reload
```

### VS Code 확장 개발

```bash
cd vscode-extension
npm install
npm run build        # TypeScript 컴파일
npm run package:vsix # .vsix 패키지 생성
```

## 테스트 실행

```bash
# 전체 테스트
python -m pytest tests/ -v

# 유닛 테스트만
python -m pytest tests/unit/ -v

# 특정 파일
python -m pytest tests/unit/test_tools.py -v
```

현재 테스트 현황:
- `tests/unit/test_security.py` — 인증, MIME 검증, rate limit (16개)
- `tests/unit/test_tools.py` — edit_file, grep, todo, read_file, 샌드박스 (23개)

PR에는 변경사항에 대한 테스트를 포함해 주세요.

## PR 가이드라인

1. `main` 브랜치에서 새 브랜치를 만드세요: `git checkout -b feat/my-feature`
2. 커밋 메시지는 `feat:`, `fix:`, `docs:`, `chore:` 등의 접두사를 사용하세요
3. PR 제목과 설명에 변경 내용과 이유를 간결하게 적어 주세요
4. 가능하면 테스트를 추가해 주세요
5. `LTCAI doctor` 통과 여부를 확인해 주세요

## 코드 스타일

- Python 3.11+ 문법 사용
- `from __future__ import annotations` 상단에 포함
- 타입 어노테이션 사용 권장
- 불필요한 주석 지양 (코드가 스스로 설명하도록)

## 아키텍처 개요

[docs/architecture.md](docs/architecture.md) 참고

## 보안 취약점 제보

공개 이슈 대신 [SECURITY.md](SECURITY.md)를 통해 비공개 제보해 주세요.
