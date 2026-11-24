const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const repo = path.resolve(__dirname, "..");
const popupHtml = fs.readFileSync(
  path.join(repo, "browser-extension", "popup.html"),
  "utf8",
);
const popupSource = fs.readFileSync(
  path.join(repo, "browser-extension", "popup.js"),
  "utf8",
);
const captureSource = fs.readFileSync(
  path.join(repo, "browser-extension", "capture-page.js"),
  "utf8",
);
const {
  COPY,
  DEFAULT_LANGUAGE,
  DEFAULT_PORT,
  extractServerLanguage,
  normalizeLanguage,
  resolveLanguage,
  t,
  REQUEST_TIMEOUT_MS,
  RECALL_TIMEOUT_MS,
  approvalsView,
  fetchJsonWithTimeout,
  fetchWithTimeout,
  groundingView,
  normalizePort,
} = require("../browser-extension/popup.js");

function runCapture(text, selectedText = "", opts = {}) {
  const context = {
    Date,
    TextDecoder,
    TextEncoder,
    document: {
      title: "Unicode capture",
      body: { innerText: text },
    },
    location: { href: "https://example.test/page" },
    window: {
      getSelection() {
        if (opts.nullSelection) return null;
        return { toString: () => selectedText };
      },
    },
  };
  return vm.runInNewContext(captureSource, context);
}

test("browser extension defaults to the Lattice runtime port", () => {
  assert.equal(DEFAULT_PORT, 4825);
  assert.match(popupHtml, /id="port"[^>]*value="4825"/);
  assert.equal(normalizePort("4825"), 4825);
  assert.equal(normalizePort("0"), 4825);
  assert.equal(normalizePort("70000"), 4825);
  assert.equal(normalizePort("not-a-port"), 4825);
});

test("popup injects the byte-safe capture asset", () => {
  assert.match(popupSource, /files:\s*\["capture-page\.js"\]/);
  assert.equal(fs.existsSync(path.join(repo, "browser-extension", "capture-page.js")), true);
});

test("captured Unicode text stays within the backend's 4 MiB UTF-8 limit", () => {
  const maxBytes = 4 * 1024 * 1024;
  const payload = runCapture("한".repeat(Math.ceil(maxBytes / 3) + 10));
  const capturedBytes = Buffer.byteLength(payload.text, "utf8");

  assert.ok(capturedBytes <= maxBytes, `${capturedBytes} exceeds ${maxBytes}`);
  assert.ok(payload.text.length > 0);
  assert.equal(payload.text.includes("�"), false);
  assert.equal(payload.url, "https://example.test/page");
});

test("capture leaves text below the byte limit unchanged", () => {
  const text = "Lattice 한글 🧠";
  assert.equal(runCapture(text).text, text);
});

test("local requests are aborted at the configured timeout", async () => {
  let signal;
  const pendingFetch = (_url, options) => {
    signal = options.signal;
    return new Promise((_resolve, reject) => {
      signal.addEventListener(
        "abort",
        () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        },
        { once: true },
      );
    });
  };

  await assert.rejects(
    fetchWithTimeout("http://127.0.0.1:4825/health", {}, 5, pendingFetch),
    (error) => error.name === "AbortError",
  );
  assert.equal(signal.aborted, true);
  assert.equal(REQUEST_TIMEOUT_MS, 30_000);
});

test("a completed request clears its abort timer", async () => {
  let signal;
  const response = await fetchWithTimeout(
    "http://127.0.0.1:4825/health",
    {},
    5,
    async (_url, options) => {
      signal = options.signal;
      return { ok: true };
    },
  );

  assert.equal(response.ok, true);
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.equal(signal.aborted, false);
});

// ── v9.9.7: recall + approval visibility ─────────────────────────────────────
// The browser extension was capture-only. It now asks the same `/chat` surface
// every other client uses and renders the server's own grounding verdict — it
// never computes one locally, and never promotes an absent verdict.

test("popup markup exposes the recall and approval surfaces", () => {
  assert.match(popupHtml, /id="question"/);
  assert.match(popupHtml, /id="ask"/);
  assert.match(popupHtml, /data-testid="grounding"/);
  assert.match(popupHtml, /data-testid="approvals"/);
});

test("a supported verdict names the sources the answer used", () => {
  const view = groundingView({
    grounding: { status: "supported", cited: [{ title: "예산 계획" }, { title: "회의 메모" }] },
  });
  assert.equal(view.kind, "supported");
  assert.match(view.text, /예산 계획/);
  assert.match(view.text, /회의 메모/);
});

test("unsupported and no_context read as not grounded, with the server's reason", () => {
  for (const status of ["unsupported", "no_context"]) {
    const view = groundingView({ grounding: { status, reason: "검색된 출처가 없습니다" } });
    assert.equal(view.kind, "none");
    assert.match(view.text, /검색된 출처가 없습니다/);
  }
});

test("an absent or unknown verdict is never rendered as grounded", () => {
  for (const payload of [null, {}, { grounding: null }, { grounding: {} }, { grounding: { status: "weird" } }]) {
    const view = groundingView(payload);
    assert.equal(view.kind, "unknown");
    assert.doesNotMatch(view.text, /근거 있음/);
  }
});

test("pending approvals surface only when runs are actually waiting", () => {
  assert.equal(approvalsView({ pending: [] }), "");
  assert.equal(approvalsView(null), "");
  assert.equal(approvalsView({ pending: [{ nope: 1 }] }), "");
  assert.match(approvalsView({ pending: [{ run_id: "a" }, { run_id: "b" }] }), /2건/);
});

test("recall gets a longer timeout than a capture round-trip", () => {
  assert.ok(RECALL_TIMEOUT_MS > REQUEST_TIMEOUT_MS);
});

test("the extension still posts only to the local runtime", () => {
  assert.doesNotMatch(popupSource, /https?:\/\/(?!127\.0\.0\.1)/);
});

// ── the popup speaks one language at a time ─────────────────────────────
//
// It used to speak two: the capture status was English ("Added to Knowledge
// Graph"), the grounding badge and the approvals line were Korean. Both were
// on screen together, so whichever language you read, half the popup was in
// the other one.

test("every popup message exists in every language", () => {
  const languages = Object.keys(COPY);
  assert.ok(languages.length >= 2, "a catalog with one language is not a catalog");
  const keys = Object.keys(COPY[DEFAULT_LANGUAGE]);
  assert.ok(keys.length > 0);
  for (const language of languages) {
    for (const key of keys) {
      assert.ok(COPY[language][key], `${language} is missing ${key}`);
    }
    assert.deepEqual(
      Object.keys(COPY[language]).sort(),
      keys.slice().sort(),
      `${language} has keys the default language does not`,
    );
  }
});

test("no English entry contains Korean, and no Korean entry is untranslated English", () => {
  const hangul = /[가-힣]/;
  for (const [key, value] of Object.entries(COPY.en)) {
    assert.ok(!hangul.test(value), `en.${key} still contains Korean: ${value}`);
  }
  for (const [key, value] of Object.entries(COPY.ko)) {
    // Placeholder-only and symbol-only strings legitimately match in both.
    if (!/[A-Za-z]/.test(value)) continue;
    assert.notEqual(value, COPY.en[key], `ko.${key} is a copy of the English text`);
  }
});

test("language resolves from the browser and never to an unsupported one", () => {
  assert.equal(resolveLanguage(null, "en-GB"), "en");
  assert.equal(resolveLanguage(null, "ko-KR"), "ko");
  assert.equal(resolveLanguage(null, "fr-FR"), DEFAULT_LANGUAGE);
  assert.equal(resolveLanguage(null, ""), DEFAULT_LANGUAGE);
  assert.equal(resolveLanguage(null, undefined), DEFAULT_LANGUAGE);
  assert.equal(resolveLanguage("en", "ko-KR"), "en", "a stored choice outranks the browser");
});

test("normalizeLanguage accepts region tags and rejects everything else", () => {
  assert.equal(normalizeLanguage("EN_us"), "en");
  assert.equal(normalizeLanguage("ko"), "ko");
  assert.equal(normalizeLanguage("zh-CN"), null);
  assert.equal(normalizeLanguage(""), null);
  assert.equal(normalizeLanguage(null), null);
});

test("t interpolates and falls back to the key rather than showing nothing", () => {
  assert.equal(t("en", "approvals.pending", { count: 2 }), "⏳ 2 run(s) waiting for approval — approve them in the web app");
  assert.equal(t("ko", "approvals.pending", { count: 2 }), "⏳ 승인 대기 중인 실행 2건 — 웹 앱에서 승인하세요");
  assert.equal(t("en", "no.such.key"), "no.such.key");
});

test("the grounding badge speaks the popup's language", () => {
  const payload = { grounding: { status: "supported", cited: [{ title: "notes.md" }] } };
  assert.match(groundingView(payload, "en").text, /^Grounded — notes\.md$/);
  assert.match(groundingView(payload, "ko").text, /^근거 있음 — notes\.md$/);

  const none = { grounding: { status: "unsupported", reason: "no sources" } };
  assert.match(groundingView(none, "en").text, /^Not grounded — no sources$/);

  // An absent verdict is still "unknown", in whichever language — the badge
  // must never upgrade silence into evidence.
  assert.equal(groundingView({}, "en").kind, "unknown");
  assert.equal(groundingView({}, "ko").kind, "unknown");
});

test("the approvals line speaks the popup's language", () => {
  const payload = { pending: [{ run_id: "r1" }, { run_id: "r2" }] };
  assert.match(approvalsView(payload, "en"), /2 run\(s\) waiting/);
  assert.match(approvalsView(payload, "ko"), /승인 대기 중인 실행 2건/);
  assert.equal(approvalsView({ pending: [] }, "en"), "");
});

test("popup.js carries no user-visible string outside the catalog", () => {
  // A literal added at a call site is how the popup became bilingual the
  // first time. Korean is the tell: the catalog is the only place it belongs.
  const body = popupSource.slice(popupSource.indexOf("function normalizePort"));
  const hangulLines = body
    .split("\n")
    .filter((line) => /[가-힣]/.test(line) && !line.trim().startsWith("//"));
  assert.deepEqual(hangulLines, [], `user-visible Korean outside COPY:\n${hangulLines.join("\n")}`);
});

test("every local request tells the server which language to answer in", () => {
  // The server has its own message catalog now; without this header it falls
  // back to Accept-Language, which is the browser's install language rather
  // than the one this popup is showing.
  assert.match(popupSource, /X-Lattice-Language/);
  const localHeaderUses = popupSource.match(/localHeaders\(/g) || [];
  assert.ok(localHeaderUses.length >= 4, "some local call still sends bare headers");
});

test("extractServerLanguage syncs supported language from server settings or features", () => {
  assert.equal(extractServerLanguage(null), null);
  assert.equal(extractServerLanguage({ language: "en-US" }), "en");
  assert.equal(
    extractServerLanguage({
      features: [{ id: "ui_language", current: "ko-KR", default: "en" }],
    }),
    "ko",
  );
  assert.equal(
    extractServerLanguage({
      features: [{ id: "ui_language", current: "fr-FR" }],
    }),
    null,
  );
});

test("capture handles null getSelection without throwing and enforces UTF-8 byte limit on selection", () => {
  const nullSel = runCapture("Visible text", "", { nullSelection: true });
  assert.equal(nullSel.text, "Visible text");
  assert.equal(nullSel.selected_text, "");

  const bigSelected = runCapture("Body", "한".repeat(100_000));
  assert.ok(Buffer.byteLength(bigSelected.selected_text, "utf8") <= 200_000);
  assert.equal(bigSelected.selected_text.includes("\uFFFD"), false);
});

test("capture filters hidden and script elements on live DOM via TreeWalker", () => {
  const body = { tagName: "BODY", parentElement: null };
  const visibleEl = { tagName: "P", parentElement: body, hidden: false, getAttribute: () => null };
  const hiddenEl = { tagName: "DIV", parentElement: body, hidden: false, getAttribute: () => null, _display: "none" };
  const ariaHiddenEl = { tagName: "SPAN", parentElement: body, hidden: false, getAttribute: (k) => (k === "aria-hidden" ? "true" : null) };
  const scriptEl = { tagName: "SCRIPT", parentElement: body, hidden: false, getAttribute: () => null };
  const nodes = [
    { nodeValue: "Hello visible", parentElement: visibleEl },
    { nodeValue: "Secret hidden", parentElement: hiddenEl },
    { nodeValue: "Screen reader hidden", parentElement: ariaHiddenEl },
    { nodeValue: "console.log(1)", parentElement: scriptEl },
  ];
  let idx = 0;
  const payload = vm.runInNewContext(captureSource, {
    Date,
    TextDecoder,
    TextEncoder,
    NodeFilter: { SHOW_TEXT: 4 },
    document: {
      title: "Live DOM",
      body,
      createTreeWalker: () => ({ nextNode: () => nodes[idx++] || null }),
    },
    location: { href: "https://example.test/live" },
    window: {
      getSelection: () => null,
      getComputedStyle: (el) => ({ display: el._display || "block", visibility: "visible" }),
    },
  });
  assert.equal(payload.text, "Hello visible");
});

test("fetchWithTimeout forwards caller AbortSignal and fetchJsonWithTimeout guards slow body reads", async () => {
  const caller = new AbortController();
  let seenSignal;
  const p = fetchWithTimeout(
    "http://127.0.0.1:4825/health",
    { signal: caller.signal },
    5_000,
    (_url, options) => {
      seenSignal = options.signal;
      return new Promise((_res, rej) => {
        options.signal.addEventListener("abort", () => rej(new Error("caller-aborted")));
      });
    },
  );
  caller.abort();
  await assert.rejects(p, /caller-aborted/);
  assert.equal(seenSignal.aborted, true);

  // Body read timeout while headers already arrived
  await assert.rejects(
    fetchJsonWithTimeout(
      "http://127.0.0.1:4825/chat",
      {},
      15,
      async (_url, options) => ({
        ok: true,
        status: 200,
        json: () =>
          new Promise((_res, rej) => {
            options.signal.addEventListener("abort", () => {
              const e = new Error("body timed out");
              e.name = "AbortError";
              rej(e);
            });
          }),
      }),
    ),
    (err) => err.name === "AbortError",
  );
});
