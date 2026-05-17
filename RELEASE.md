# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity` 배포를 한 번에 처리하기 위한 체크리스트입니다.

## 1) 공통 준비

1. 버전 업데이트
   - `package.json` (root)
   - `pyproject.toml`
   - `vscode-extension/package.json`
2. 루트에서 빌드/기본 검증
   - `npm run check:python`
   - `npm run build:python`

## 2) npm 배포

1. 로그인
   - `npm login`
2. 배포
   - `npm run publish:npm`

## 3) PyPI 배포

1. 업로드 도구 설치
   - `python3 -m pip install --upgrade build twine`
2. 빌드
   - `npm run build:python`
3. 업로드
   - `npm run publish:pypi`

참고:
- TestPyPI 먼저 쓰려면:
  - `python3 -m twine upload --repository testpypi dist/*`

## 4) VS Code / Cursor / Antigravity 확장 배포

`vscode-extension` 디렉터리 기준:

1. 의존성 설치 및 빌드
   - `npm install`
   - `npm run build`
2. VSIX 생성
   - `npm run package:vsix`
3. VS Code Marketplace 배포
   - `npm run publish:vscode`
4. Open VSX 배포 (Cursor/일부 포크 호환)
   - `npm run publish:openvsx`
5. 로컬 설치 (VS Code/Cursor/Antigravity)
   - `npm run install:all`

토큰:
- VS Code Marketplace: `vsce login <publisher>`
- Open VSX: `ovsx create-namespace <publisher>` / `ovsx publish ... -p <token>`

## 5) Antigravity/Cursor 관련 메모

- `Cursor`, `Antigravity`는 VSIX 설치가 가능하므로 `install:all`로 로컬 검증 가능.
- 원격 “스토어 등록”은 해당 스토어 정책/토큰이 필요합니다.
- 스토어 API/토큰 준비 후에는 같은 VSIX를 재사용해 등록하면 됩니다.

