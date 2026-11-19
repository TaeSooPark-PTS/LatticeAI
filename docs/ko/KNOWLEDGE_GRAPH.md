[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../KNOWLEDGE_GRAPH.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./KNOWLEDGE_GRAPH.md)

# 지식 그래프 (Knowledge Graph)

Knowledge Graph는 Lattice AI Brain 내부의 영속적인 인프라입니다.

제품의 본질은 Brain입니다. 그래프는 기억, 지식, 출처, 관계가 어떻게 연결되는지 직접 확인하고 싶을 때 사용자가 열어볼 수 있는 가장 깊은 구조화 계층입니다. 따라서 그래프는 첫 화면이나 주된 제품 표면으로 전면에 나서기보다 Brain 경험에서 자연스럽게 자라나야 합니다.

## 저장 대상

- 파일(files)
- 문서(documents)
- 이미지(images)
- 스크린샷(screenshots)
- 대화(conversations)
- 노트(notes)
- 의사결정(decisions)
- 작업 이력(work history)
- 생성된 아티팩트(generated artifacts)
- 근거 링크(evidence links)

## 파이프라인

```text
원본 자료 (source material)
  -> 멀티모달 이해 (multimodal understanding)
  -> 엔티티 추출 (entity extraction)
  -> 관계 추출 (relationship extraction)
  -> 근거 저장 (evidence storage)
  -> 그래프 업데이트 (graph update)
  -> 컨텍스트 조립 (context assembly)
  -> Brain 회상, 대화, 분석, 문서 생성, 자동화 (Brain recall, conversation, analysis, documents, automation)
```

## 설계 규칙

- Brain 우선 UX 계약을 유지합니다: Brain -> Memories -> Knowledge -> Relationships -> Graph.
- 그래프 내부 메커니즘을 기본 사용자 여정으로 노출하지 않습니다.
- 레거시 읽기 호환성을 보존합니다.
- 파괴적 변이(destructive mutation)보다 재투영(reprojection)을 우선합니다.
- 그래프 사실(fact)과 함께 항상 근거(evidence)를 보관합니다.
- 롤백 경로를 항상 사용 가능하게 유지합니다.
- 모델을 언제든 교체 가능한 Brain 워커로 취급합니다.
