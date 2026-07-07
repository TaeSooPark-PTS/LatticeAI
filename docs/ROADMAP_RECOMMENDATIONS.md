# Strategic Roadmap Recommendations

This roadmap captures the 2026-06-20 product direction input and maps it to
small release-sized work. The operating principle stays unchanged:

> Models are temporary. Knowledge is durable. The Brain is the product.

## 7.3.0 Applied Slice

7.3.0 implements the first narrow slice of the roadmap:

- Runtime evolution: single-agent and multi-agent execution share
  `agent-run-contract/v1`, making mode/status/timeline evidence inspectable.
- Hybrid search optimization: the Brain quality gate now includes deterministic
  recall and ranking regression thresholds.
- Security and trust: run contracts distinguish runtime type and execution mode
  so simulated output is not presented as real execution.

## 7.4.0 Applied Slice

7.4.0 completes the next roadmap slice without deferring it:

- Runtime convergence: agent runs, workflow runs, audit events, and realtime
  events all expose the `agent-run-contract/v1` family envelope while keeping
  legacy top-level fields for compatibility.
- Trust and operations: persisted run rows refresh their contract through
  queued, running, terminal, cancelled, and interrupted states; audit events are
  contracted only after secret redaction.
- Retrieval quality and scale: the CI quality gate now seeds a real local
  Knowledge Graph corpus and scores SearchService hybrid retrieval with judged
  queries, recall, precision, NDCG, and must-include hit-rate thresholds.

## 7.5.0 Applied Slice

7.5.0 burns down the remaining 7.4.0 risk instead of deferring it:

- Contract consumption: AgentRuntime and realtime feed APIs now emit compact
  `contracts` views so UI, replay, admin, and export consumers can depend on the
  shared family envelope directly.
- Retrieval scale: the corpus fixture now runs against 250+ local records while
  keeping judged queries, graded relevance, and must-include expectations.
- Release trust: stale artifact mixing is handled through clean exact-version
  artifact generation, npm audit findings are cleared, and the Tauri 2 stack is
  updated past the old `block v0.1.6` future-incompatibility warning.

## Near-Term Tracks

1. Retrieval quality and scale
   - Add latency budgets for larger corpora.
   - Track per-channel keyword/vector/graph diagnostics in CI.
   - Tune semantic/graph/keyword fusion by query class.

2. Incremental ingestion
   - Add background indexing jobs.
   - Detect duplicates and merge memory candidates.
   - Surface conflict resolution for contradictory memories.

3. Runtime convergence
   - Migrate remaining UI run/replay/admin components to read the API-level
     compact contract views as their primary source.
   - Keep simulation mode explicit and never record it as product success.
   - Route tools through explicit registry/governance contracts.

4. Brain SDK boundary
   - Continue extracting `lattice_brain` as a reusable Brain Core package.
   - Keep compatibility shims until downstream imports are migrated.
   - Preserve migration rollback paths for graph and memory storage.

5. Trust and operations
   - Extend audit logging around tool execution and retrieval injection.
   - Add dependency vulnerability monitoring.
   - Add Tauri update/rollback planning before enabling auto-update.

## Longer-Term Tracks

- Multi-modal ingestion and retrieval for images, audio, and video.
- Proactive Brain synthesis, contradiction detection, and recommendations.
- Temporal reasoning over historical graph states.
- Interoperability with Obsidian, Notion, Email, Calendar, Git, Slack, and Teams.
- Encrypted Brain Network sharing.
- Plugin marketplace and public benchmarks.

## Large-Scale Work Feasibility Assessment (2026-07-07)

2026-07-07 코드베이스 검사 + 테스트 실행 결과 기반. (app_factory 1214줄, workspace_os 1391줄, 최근 AgentRuntime facade + KG mixin 분해 완료, 833 unit tests passed)

### 1. Knowledge Graph / Retrieval at Large Corpus Scale (가장 추천, 기반 탄탄)
- Incremental/background ingestion + advanced dedup(콘텐츠해시 이상), conflict resolution, merge.
- Larger corpus benchmarks (현재 250+ → 수천~수만 아이템), latency budget, channel별 진단.
- pgvector 풀 프로덕션 경로 + 마이그레이션.
- **실행 가능성**: IngestionPipeline + provenance + vector mixin + benchmark fixtures 이미 존재. discovery/retrieval 분해 완료. hooks lifecycle 통합됨. 대형 작업으로 적합. 테스트: test_ingestion_pipeline.py 14/14 green.

### 2. Multi-Modal Brain (스키마 준비됨, 구현 공백 큼)
- 이미지: vision LLM describe + 임베딩, IMAGE/IMAGE_TEXT 노드 + CONTAINS_IMAGE 엣지 완성, UI 증거 표시.
- 오디오(전사), 비디오(키프레임).
- **실행 가능성**: schema.py에 IMAGE/IMAGE_TEXT/CONTAINS_IMAGE 정의됨. discovery_index.py에 PIL + pytesseract OCR (include_ocr) 부분 구현. embeddings.py / LLMRouter에 vision 지원 부재. retrieval도 이미지 노드 미처리. ingestion 라우팅 확장 필요. 대형 + 차별화 포인트.

### 3. Server / Runtime Composition 완전화 (구조 부채 해소)
- app_factory.py 잔여 로직 추가 추출 (더 많은 wiring → runtime/* 전용 모듈).
- dict(locals()) 레거시 제거, 강력한 RuntimeContext / DI.
- Config 중앙화 감사 및 강제 (core/config.py 이미 중앙).
- **실행 가능성**: 최근 review0707 + decomp (1214줄로 감소, runtime/ 디렉토리 풍부) 후에도 여전히 1.2k 라인. AGENTS.md 우선순위 #3,4 정확히 매치. AgentRuntime은 lattice_brain/runtime/agent_runtime.py 로 facade 추출 완료. ToolRegistry도 core/ + services 분리 진행.

### 4. Proactive Synthesis + Temporal / Contradiction (최근 작업 연장)
- 백그라운드 합성 잡, 그래프 이력 기반 모순 검출, temporal 쿼리.
- 최근 커밋(Brain synthesis memories, follow-up) 을 스케줄 + 자동화로 확장.
- **실행 가능성**: MultiAgentOrchestrator + workflow_engine + IngestionPipeline + Brain Brief 이미 wired. hooks/audit 있음. agent synthesis UI 표면 최근 추가. 대형 기능으로 자연스러운 확장.

### 5. Ecosystem / Interop + Plugin Marketplace (외부 연동 대형)
- Obsidian/Notion/Email/Calendar import bridge (MCP 또는 전용 ingestion).
- 서명된 플러그인, 원격 카탈로그, marketplace UI.
- Brain Network (암호화 공유).
- **실행 가능성**: plugins/ (hello-world, git-insights), mcp_registry, marketplace.py, core/plugins.py 존재. ingestion이 단일 관문. 하지만 실제 외부 브릿지/마켓플레이스 UI/보안 스캔은 대형.

기타: Tauri auto-update + native scale, Workflow Designer 프론트엔드 완성, VSCode 확장 심화 통합 등.

**검증 결과 (2026-07-07)**:
- Core imports (Config, IngestionPipeline, AgentRuntime, ToolRegistry): OK
- Unit tests: 833 passed (tests/unit/)
- Ingestion specific: 14 passed
- Agent runtime tests: 29 passed
- Frontend lint: typecheck + privacy + OpenAPI 335 paths + lint all pass
- Git: clean (inspection 시점)

**2026-07-07 pts_grok 대형 후보 1~5 슬라이스 실행 결과 (자율 진행)**:
- 1. KG/Retrieval: background ingestion queue seam, schedule_background, vector index scale diagnostics, backlog reasons/samples, coverage ratio, and rebuild latency budget.
- 2. Multimodal: offline VisionStub describe/embed path and discovery_index vision_caption fallback so image files have searchable evidence without remote calls.
- 3. Server decomp: explicit _RUNTIME_BUNDLE in app_factory as the migration contract toward DI while dict(locals()) compatibility remains.
- 4. Proactive/Temporal: stronger MemoryQualityManager conflict detection, including pairwise opposite-preference and temporal-negation flags.
- 5. Interop/Marketplace: ingestion_bridge marketplace templates and /marketplace/interop/bridges exposure for future Obsidian/Calendar-style connector imports through unified-ingestion.
- 총 affected tests: 45 passed in the first targeted gate. Broader lint/static/doc checks are run before commit.
- 남은: 각 슬라이스는 기반 확장. full background workers, real local VLM/image embeddings, full locals() removal, graph-level temporal queries, and production connector installs remain follow-up work.

이 항목들은 "대형사이즈" 에도 할 만하며, mission (local-first, KG, AgentRuntime, security) 과 AGENTS 우선 리팩토링 순서에 부합. 작은 슬라이스로 시작 추천.
