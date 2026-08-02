/**
 * VS Code surface-parity helpers (v9.9.6).
 *
 * Review 2026-07-27 P0 #1: "VS Code: 회상 grounding 배지, Review Center(또는
 * 최소한 pending proposals), 가능하면 step 요약". `extension.ts` needs the
 * editor host, so the parity decisions live in the pure `surface.ts` module
 * and are asserted here against the exact sidecar payload shapes.
 *
 * House rules verified: the extension never invents a grounding verdict the
 * server did not issue, never upgrades a NEEDS_REVIEW run into success, and
 * drops un-actionable proposal rows instead of rendering dead entries.
 */
const assert = require("node:assert/strict");
const test = require("node:test");

const {
  groundingBadge,
  groundingLine,
  parseProposals,
  runReport,
  summarizeRun,
} = require("../vscode-extension/out/surface.js");

test("supported grounding reads as grounded and names its sources", () => {
  const badge = groundingBadge({
    grounding: {
      status: "supported",
      cited: [{ id: "n1", title: "예산 계획" }, { id: "n2", title: "회의 메모" }],
    },
  });
  assert.equal(badge.status, "supported");
  assert.equal(badge.icon, "$(check)");
  assert.deepEqual(badge.sources, ["예산 계획", "회의 메모"]);
  assert.match(groundingLine({ grounding: { status: "supported", cited: [{ title: "예산 계획" }] } }), /예산 계획/);
});

test("unsupported and no_context both read as not grounded", () => {
  for (const status of ["unsupported", "no_context"]) {
    const badge = groundingBadge({ grounding: { status, reason: "검색된 출처가 없습니다" } });
    assert.equal(badge.icon, "$(warning)");
    assert.deepEqual(badge.sources, []);
  }
});

test("a missing verdict is unknown, never grounded", () => {
  for (const payload of [null, {}, { grounding: {} }, { grounding: { status: "" } }]) {
    const badge = groundingBadge(payload);
    assert.equal(badge.status, "unknown");
    assert.equal(badge.icon, "$(question)");
  }
});

test("proposals parse from the wrapped list and keep their target path", () => {
  const rows = parseProposals({
    items: [
      {
        id: "item-1",
        title: "Update README.md",
        payload: { path: "README.md", change_class: "modify_existing" },
        created_at: "2026-07-27T00:00:00Z",
      },
      { id: "", title: "no id" },
      { id: "item-2", provenance: { path: "src/app.py", change_class: "delete" } },
    ],
  });
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    id: "item-1",
    title: "Update README.md",
    path: "README.md",
    changeClass: "modify_existing",
    createdAt: "2026-07-27T00:00:00Z",
  });
  // Falls back to the path as the label when the item has no title.
  assert.equal(rows[1].title, "src/app.py");
  assert.equal(rows[1].changeClass, "delete");
});

test("proposals tolerate a bare list and garbage payloads", () => {
  assert.equal(parseProposals([{ id: "a", title: "A" }]).length, 1);
  for (const bad of [null, undefined, 3, "x", {}]) {
    assert.deepEqual(parseProposals(bad), []);
  }
});

test("a NEEDS_REVIEW run is never summarized as success", () => {
  const summary = summarizeRun({
    status: "failed",
    final_state: "NEEDS_REVIEW",
    explanation: {
      ok: false,
      headline: { ko: "완료로 처리하지 않았습니다.", en: "Not marked complete." },
      details: [{ ko: "형식을 2번 틀렸습니다.", en: "Format broke twice." }],
    },
    steps: [
      { state: "EXECUTING", action: "write_file", args: { path: "a.html" }, result: { path: "a.html" } },
      { state: "EXECUTING", action: "parse_error" },
      { state: "VERIFYING", verdict: "PASS" },
    ],
    created_files: [{ path: "a.html" }],
  });
  assert.equal(summary.ok, false);
  assert.equal(summary.headline, "완료로 처리하지 않았습니다.");
  assert.deepEqual(summary.details, ["형식을 2번 틀렸습니다."]);
  assert.deepEqual(summary.files, ["a.html"]);
  // parse_error steps carry no execution evidence and are not listed as work.
  assert.equal(summary.steps.length, 1);
  assert.match(summary.steps[0], /write_file — a\.html/);
});

test("DONE without an explanation still reads as success", () => {
  assert.equal(summarizeRun({ final_state: "DONE", steps: [] }).ok, true);
  assert.equal(summarizeRun({ final_state: "FAILED", steps: [] }).ok, false);
});

test("proposed and failed steps get distinct markers", () => {
  const summary = summarizeRun({
    final_state: "DONE",
    steps: [
      { state: "EXECUTING", action: "write_file", args: { path: "x.md" }, result: { proposed: true } },
      { state: "EXECUTING", action: "run_command", error: "BLOCKED: destructive" },
    ],
  });
  assert.match(summary.steps[0], /git-pull-request/);
  assert.match(summary.steps[1], /circle-slash/);
});

test("the run report carries state, headline, steps and files", () => {
  const report = runReport({
    final_state: "DONE",
    explanation: { ok: true, headline: { ko: "끝났습니다.", en: "Done." }, details: [] },
    steps: [{ state: "EXECUTING", action: "write_file", args: { path: "a.md" }, result: {} }],
    created_files: ["a.md"],
  });
  assert.match(report, /DONE/);
  assert.match(report, /끝났습니다\./);
  assert.match(report, /write_file/);
  assert.match(report, /a\.md/);
  assert.match(runReport({ final_state: "DONE" }, "en"), /DONE/);
});

test("English language selection picks the English explanation strings", () => {
  const summary = summarizeRun(
    { final_state: "NEEDS_REVIEW", explanation: { ok: false, headline: { ko: "한국어", en: "English" }, details: [] } },
    "en",
  );
  assert.equal(summary.headline, "English");
});

// ── v9.9.7: evidence → action + live step timeline ───────────────────────────
// The 9.9.6 SURFACE_PARITY gaps for VS Code, closed. Same sidecar payloads the
// web app consumes; the editor only renders them differently.

const { citedSourceIds, parseEvidenceActions, stepLine } = require("../vscode-extension/out/surface.js");

test("cited source ids come from the same grounding verdict the badge uses", () => {
  assert.deepEqual(
    citedSourceIds({ grounding: { status: "supported", cited: [{ id: "n1" }, { id: "n2" }] } }),
    ["n1", "n2"],
  );
  // Older payloads carry only source_ids.
  assert.deepEqual(
    citedSourceIds({ grounding: { status: "supported", source_ids: ["n3"] } }),
    ["n3"],
  );
  for (const bad of [null, {}, { grounding: {} }, { grounding: { cited: "nope" } }]) {
    assert.deepEqual(citedSourceIds(bad), []);
  }
});

test("evidence actions parse with localized labels and drop unusable rows", () => {
  const payload = {
    actions: [
      { id: "summary", kind: "chat", label: { ko: "요약 만들기", en: "Summarize" }, prompt: "P1" },
      { id: "document", kind: "file", label: { ko: "문서", en: "Document" }, prompt: "P2", suggested_path: "a.md" },
      { id: "broken", kind: "chat", label: { ko: "x", en: "x" } },
      { kind: "chat", label: { ko: "y", en: "y" }, prompt: "P3" },
    ],
  };
  const ko = parseEvidenceActions(payload, "ko");
  assert.deepEqual(ko.map((a) => a.id), ["summary", "document"]);
  assert.equal(ko[0].label, "요약 만들기");
  assert.equal(ko[1].suggestedPath, "a.md");
  assert.equal(parseEvidenceActions(payload, "en")[0].label, "Summarize");
  assert.deepEqual(parseEvidenceActions(null), []);
});

test("live step frames render with their phase, event and detail", () => {
  assert.match(stepLine({ phase: "plan", event: "planned", steps: 3 }), /plan\/planned/);
  assert.match(stepLine({ phase: "execute", event: "tool", action: "write_file", path: "a.html", ok: true }), /write_file/);
  assert.match(stepLine({ phase: "execute", event: "tool", action: "x", ok: false }), /failed/);
  assert.match(stepLine({ phase: "execute", event: "blocked", reason: "destructive" }), /circle-slash/);
  assert.match(stepLine({ phase: "verify", event: "verdict", verdict: "PASS" }), /verdict=PASS/);
  // Unknown events stay visible rather than vanishing.
  assert.match(stepLine({ phase: "future", event: "brand_new" }), /future\/brand_new/);
  assert.match(stepLine(null), /run\/step/);
});

// ── v10.4.0 surface parity: artifact cards + model recommendation ────────────
//
// SURFACE_PARITY listed VS Code artifacts and model choice as ◐. Both gaps
// were rendering gaps, not contract gaps: the sidecar already reported
// `artifacts[]` honesty flags and a hardware-derived recommendation, and the
// editor was dropping them. These fix the parsing against the real shapes.

const {
  artifactReport,
  parseArtifacts,
  parseModelRecommendation,
} = require("../vscode-extension/out/surface.js");

test("artifact cards carry the honesty flags a flat file list drops", () => {
  const cards = parseArtifacts({
    artifacts: [
      { kind: "file", path: "src/app.py", filename: "app.py", bytes: 2048, previewable: true, valid: true },
      { kind: "file", path: "src/index.html", filename: "index.html", bytes: 900, repaired: true, valid: true },
      { kind: "file", path: "broken.json", filename: "broken.json", bytes: 12, valid: false },
    ],
  }, "en");

  assert.equal(cards.length, 3);
  assert.equal(cards[0].repaired, false);
  assert.match(cards[0].detail, /2 KB/);
  // A repaired scaffold must not read like clean model output.
  assert.equal(cards[1].repaired, true);
  assert.match(cards[1].label, /wand/);
  assert.match(cards[1].detail, /auto-repaired/);
  // The extension never upgrades the server's validity verdict.
  assert.equal(cards[2].valid, false);
  assert.match(cards[2].detail, /failed validation/);
});

test("artifact parsing falls back to created_files without inventing detail", () => {
  const cards = parseArtifacts({ created_files: ["notes.md", { path: "plan.md" }] }, "en");
  assert.deepEqual(cards.map((card) => card.path), ["notes.md", "plan.md"]);
  for (const card of cards) {
    assert.equal(card.bytes, 0);
    assert.match(card.detail, /no artifact detail reported/);
  }
});

test("a run that produced nothing says so rather than rendering an empty list", () => {
  assert.deepEqual(parseArtifacts({}, "en"), []);
  assert.match(artifactReport({}, "en"), /no files produced/);
  assert.match(artifactReport({}, "ko"), /생성된 파일 없음/);
});

test("artifact report lists every produced file with its caveat", () => {
  const report = artifactReport({
    artifacts: [{ path: "a.py", filename: "a.py", bytes: 10, repaired: true, valid: true }],
  }, "en");
  assert.match(report, /a\.py/);
  assert.match(report, /auto-repaired/);
});

test("model recommendation is read from the server, never recomputed", () => {
  const advice = parseModelRecommendation({
    zero_config: {
      recommend: {
        model_id: "mlx-community/gemma-3-12b-it-4bit",
        runtime: "mlx",
        rationale: ["RAM 32GB", "실제 다운로드 및 로드 가능한 mlx 모델"],
      },
    },
  });
  assert.equal(advice.modelId, "mlx-community/gemma-3-12b-it-4bit");
  assert.equal(advice.runtime, "mlx");
  assert.equal(advice.rationale.length, 2);
});

test("no recommendation means no banner, not a made-up one", () => {
  assert.equal(parseModelRecommendation({}), null);
  assert.equal(parseModelRecommendation({ zero_config: {} }), null);
  assert.equal(parseModelRecommendation({ zero_config: { recommend: { runtime: "mlx" } } }), null);
  assert.equal(parseModelRecommendation(null), null);
});
