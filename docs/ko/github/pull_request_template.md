[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../../.github/pull_request_template.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./pull_request_template.md)

# 풀 리퀘스트 템플릿 (Pull Request Template)

> 이 문서는 [`.github/pull_request_template.md`](../../../.github/pull_request_template.md)의 한국어 안내 미러입니다.

## 요약 (Summary)

- 무엇이 변경되었는지 설명해 주세요
- 왜 변경되었는지 설명해 주세요

## 테스트 (Testing)

- [ ] `python -m pytest tests/unit/ -v`
- [ ] `python -m pytest tests/integration/ -v`
- [ ] `LTCAI doctor`

## 체크리스트 (Checklist)

- [ ] 동작이 변경된 경우 관련 문서를 업데이트했습니다
- [ ] UI가 변경된 경우 스크린샷을 첨부했습니다
- [ ] 시크릿, 로컬 런타임 상태 또는 생성된 아티팩트가 커밋에 포함되지 않았습니다
