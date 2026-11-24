// Lattice AI browser extension — popup logic.
//
// Capture: sends the active tab's readable content to the LOCAL runtime
// (http://127.0.0.1:<port>/api/browser/ingest-current-tab).
// Recall (v9.9.7): asks the same `/chat` surface every other client uses and
// renders the server's own grounding verdict.
// Approvals (v9.9.7): shows how many agent runs are waiting, so a paused run
// is never invisible from here.
//
// Nothing leaves the machine; there is no cloud endpoint anywhere in this
// extension, and no verdict is computed locally — the badge only reports what
// the server said.

const DEFAULT_PORT = 4825;
const REQUEST_TIMEOUT_MS = 30_000;
// Recall runs a model; it is legitimately slower than an ingest round-trip.
const RECALL_TIMEOUT_MS = 120_000;

// ── copy ─────────────────────────────────────────────────────────────────
//
// The popup used to mix languages inside one window: the capture status was
// English ("Added to Knowledge Graph"), the grounding badge and the approvals
// line were Korean ("근거 있음", "승인 대기 중인 실행 N건"). Whoever was using
// it read half of it in a language they had not chosen — the same problem the
// web app solves with its own catalog, on a surface that had none.
//
// Plain object, no build step: this file ships as-is to the browser.

const COPY = {
  ko: {
    "capture.busy": "페이지를 읽는 중…",
    "capture.sending": "로컬 Lattice AI로 보내는 중…",
    "capture.duplicate": "이미 기억에 있습니다 ✓",
    "capture.added": "기억에 추가했습니다 ✓",
    "capture.rejected": "추가하지 못했습니다: {detail}",
    "capture.noTab": "열려 있는 탭을 찾지 못했습니다.",
    "capture.noContent": "이 페이지에서 읽을 수 있는 내용을 찾지 못했습니다.",
    "capture.failed": "실패: {message} · Lattice AI가 켜져 있고 로그인되어 있는지 확인해 주세요.",
    "capture.timeout": "로컬 Lattice AI 요청이 30초 안에 끝나지 않았습니다",
    "recall.busy": "Brain에게 묻는 중…",
    "recall.empty": "(빈 응답)",
    "recall.failed": "회상 실패: {message} · Lattice AI가 켜져 있고 로그인되어 있는지 확인해 주세요.",
    "grounding.supported": "근거 있음",
    "grounding.supportedWith": "근거 있음 — {sources}",
    "grounding.none": "근거 없음",
    "grounding.noneWith": "근거 없음 — {reason}",
    "grounding.unknown": "근거 확인 불가",
    "approvals.pending": "⏳ 승인 대기 중인 실행 {count}건 — 웹 앱에서 승인하세요",
  },
  en: {
    "capture.busy": "Reading this page…",
    "capture.sending": "Sending to local Lattice AI…",
    "capture.duplicate": "Already in your memory ✓",
    "capture.added": "Added to your memory ✓",
    "capture.rejected": "Not added: {detail}",
    "capture.noTab": "No active tab to capture.",
    "capture.noContent": "This page returned no readable content.",
    "capture.failed": "Failed: {message} · Is Lattice AI running locally, and are you signed in?",
    "capture.timeout": "The local Lattice AI request timed out after 30 seconds",
    "recall.busy": "Asking your Brain…",
    "recall.empty": "(empty response)",
    "recall.failed": "Recall failed: {message} · Is Lattice AI running locally, and are you signed in?",
    "grounding.supported": "Grounded",
    "grounding.supportedWith": "Grounded — {sources}",
    "grounding.none": "Not grounded",
    "grounding.noneWith": "Not grounded — {reason}",
    "grounding.unknown": "Grounding unknown",
    "approvals.pending": "⏳ {count} run(s) waiting for approval — approve them in the web app",
  },
};

const DEFAULT_LANGUAGE = "ko";

/** "en-GB" -> "en" when supported, else null. */
function normalizeLanguage(value) {
  const tag = String(value || "").trim().toLowerCase().replace(/_/g, "-");
  if (!tag) return null;
  const base = tag.split("-")[0];
  return Object.prototype.hasOwnProperty.call(COPY, base) ? base : null;
}

/**
 * The language this popup speaks.
 *
 * The browser's own UI language, which is the only signal available here: the
 * extension popup is a separate origin from the web app, so it cannot read the
 * app's `lattice.language`. `stored` is kept as the first-choice input for the
 * day a popup preference exists; today it is always null, and passing the
 * browser language alone is a complete answer.
 */
function resolveLanguage(stored, uiLanguage) {
  return (
    normalizeLanguage(stored)
    || normalizeLanguage(uiLanguage)
    || DEFAULT_LANGUAGE
  );
}

/** Extract server-configured language from `/api/features` or profile/settings payloads. */
function extractServerLanguage(payload) {
  if (!payload || typeof payload !== "object") return null;
  if (typeof payload.language === "string") {
    const direct = normalizeLanguage(payload.language);
    if (direct) return direct;
  }
  const features = Array.isArray(payload.features) ? payload.features : [];
  for (const item of features) {
    if (item && (item.id === "language" || item.id === "ui_language")) {
      const candidate = normalizeLanguage(item.current || item.default);
      if (candidate) return candidate;
    }
  }
  return null;
}

function t(language, key, params) {
  const table = COPY[language] || COPY[DEFAULT_LANGUAGE];
  let text = table[key] || COPY[DEFAULT_LANGUAGE][key] || key;
  for (const [name, value] of Object.entries(params || {})) {
    text = text.split("{" + name + "}").join(String(value));
  }
  return text;
}

function normalizePort(value) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535
    ? parsed
    : DEFAULT_PORT;
}

/**
 * Render the server's `/chat` grounding verdict.
 *
 * Honest by construction: an absent or unrecognized verdict is "unknown",
 * never "grounded". The extension must not invent evidence the Brain did not
 * report.
 */
function groundingView(payload, language = DEFAULT_LANGUAGE) {
  const grounding =
    payload && typeof payload === "object" && payload.grounding && typeof payload.grounding === "object"
      ? payload.grounding
      : null;
  const status = grounding && typeof grounding.status === "string" ? grounding.status : "";
  if (status === "supported") {
    const cited = Array.isArray(grounding.cited)
      ? grounding.cited
          .map((entry) => (entry && typeof entry.title === "string" ? entry.title.trim() : ""))
          .filter(Boolean)
      : [];
    return {
      status,
      kind: "supported",
      text: cited.length
        ? t(language, "grounding.supportedWith", { sources: cited.slice(0, 3).join(", ") })
        : t(language, "grounding.supported"),
    };
  }
  if (status === "unsupported" || status === "no_context") {
    const reason = grounding && typeof grounding.reason === "string" ? grounding.reason.trim() : "";
    return {
      status,
      kind: "none",
      text: reason ? t(language, "grounding.noneWith", { reason }) : t(language, "grounding.none"),
    };
  }
  return { status: status || "unknown", kind: "unknown", text: t(language, "grounding.unknown") };
}

/** Pending-approval summary line, or "" when nothing is waiting. */
function approvalsView(payload, language = DEFAULT_LANGUAGE) {
  const pending =
    payload && typeof payload === "object" && Array.isArray(payload.pending)
      ? payload.pending.filter((item) => item && typeof item === "object" && item.run_id)
      : [];
  if (!pending.length) return "";
  return t(language, "approvals.pending", { count: pending.length });
}

function buildTimeoutSignal(timeoutMs, callerSignal) {
  const controller = new AbortController();
  const onCallerAbort = () => controller.abort(callerSignal?.reason);
  if (callerSignal) {
    if (callerSignal.aborted) {
      controller.abort(callerSignal.reason);
    } else {
      callerSignal.addEventListener("abort", onCallerAbort, { once: true });
    }
  }
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const cleanup = () => {
    clearTimeout(timer);
    if (callerSignal) {
      callerSignal.removeEventListener("abort", onCallerAbort);
    }
  };
  return { signal: controller.signal, cleanup };
}

async function fetchWithTimeout(url, options, timeoutMs = REQUEST_TIMEOUT_MS, fetchImpl = fetch) {
  const { signal, cleanup } = buildTimeoutSignal(timeoutMs, options?.signal);
  try {
    return await fetchImpl(url, { ...(options || {}), signal });
  } finally {
    cleanup();
  }
}

/**
 * Fetch and consume the response body under a single end-to-end timeout so
 * stalled body streaming cannot outlive the header deadline.
 */
async function fetchJsonWithTimeout(url, options, timeoutMs = REQUEST_TIMEOUT_MS, fetchImpl = fetch) {
  const { signal, cleanup } = buildTimeoutSignal(timeoutMs, options?.signal);
  try {
    const resp = await fetchImpl(url, { ...(options || {}), signal });
    if (!resp.ok) {
      let detail = "";
      try {
        detail = typeof resp.text === "function" ? await resp.text() : "";
      } catch (readErr) {
        console.warn("Lattice AI extension: failed to read error body", readErr);
      }
      const err = new Error(detail ? `HTTP ${resp.status}: ${detail.slice(0, 200)}` : `HTTP ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
    return await resp.json();
  } finally {
    cleanup();
  }
}

async function saveStorage(values) {
  const storageSet = chrome.storage?.local?.set;
  if (typeof storageSet !== "function") return;
  try {
    await new Promise((resolve) => {
      const maybePromise = storageSet.call(chrome.storage.local, values, () => resolve());
      if (maybePromise && typeof maybePromise.then === "function") {
        maybePromise.then(resolve, resolve);
      }
    });
  } catch (err) {
    console.warn("Lattice AI extension: failed to persist storage", err);
  }
}

function initializePopup() {
  const portInput = document.getElementById("port");
  const sendBtn = document.getElementById("send");
  const statusEl = document.getElementById("status");
  if (!portInput || !sendBtn || !statusEl) return;

  function setStatus(message, kind) {
    statusEl.textContent = message;
    statusEl.className = "status" + (kind ? " " + kind : "");
  }

  const browserUiLang = chrome.i18n?.getUILanguage?.() || navigator.language;
  let language = resolveLanguage(null, browserUiLang);
  document.documentElement.lang = language;

  function applyLanguage(next) {
    const resolved = resolveLanguage(next, browserUiLang);
    language = resolved;
    document.documentElement.lang = resolved;
  }

  // Remember the last-used valid port and synced language preference.
  chrome.storage?.local.get(["latticePort", "latticeLanguage"], (result) => {
    portInput.value = String(normalizePort(result?.latticePort));
    if (result?.latticeLanguage) applyLanguage(result.latticeLanguage);
  });

  /** Headers every local call carries, so the server answers in this language too. */
  function localHeaders(extra) {
    return { "X-Lattice-Language": language, ...(extra || {}) };
  }

  sendBtn.addEventListener("click", async () => {
    sendBtn.disabled = true;
    setStatus(t(language, "capture.busy"));
    const port = normalizePort(portInput?.value);
    if (portInput) portInput.value = String(port);
    await saveStorage({ latticePort: port });

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id) throw new Error(t(language, "capture.noTab"));
      const [{ result: payload } = {}] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["capture-page.js"],
      });
      if (!payload || typeof payload !== "object") {
        throw new Error(t(language, "capture.noContent"));
      }

      setStatus(t(language, "capture.sending"));
      const data = await fetchJsonWithTimeout(
        `http://127.0.0.1:${port}/api/browser/ingest-current-tab`,
        {
          method: "POST",
          headers: localHeaders({ "Content-Type": "application/json" }),
          credentials: "include", // reuse the local app session cookie if present
          body: JSON.stringify(payload),
        },
      );
      if (data.status === "ok") {
        setStatus(t(language, data.duplicate ? "capture.duplicate" : "capture.added"), "ok");
      } else {
        setStatus(t(language, "capture.rejected", { detail: `${data.status} ${data.detail || ""}`.trim() }), "err");
      }
    } catch (err) {
      const timedOut = err && err.name === "AbortError";
      const message = timedOut
        ? t(language, "capture.timeout")
        : err instanceof Error
          ? err.message
          : String(err);
      setStatus(t(language, "capture.failed", { message }), "err");
    } finally {
      sendBtn.disabled = false;
    }
  });

  // ── Recall (v9.9.7) ───────────────────────────────────────────────────────
  const questionEl = document.getElementById("question");
  const askBtn = document.getElementById("ask");
  const answerEl = document.getElementById("answer");
  const groundingEl = document.getElementById("grounding");

  function setGrounding(view) {
    if (!groundingEl) return;
    groundingEl.textContent = view ? view.text : "";
    groundingEl.className = view ? `badge is-${view.kind}` : "badge";
  }

  askBtn?.addEventListener("click", async () => {
    const question = String(questionEl?.value || "").trim();
    if (!question) return;
    askBtn.disabled = true;
    if (answerEl) answerEl.textContent = t(language, "recall.busy");
    setGrounding(null);
    const port = normalizePort(portInput?.value);
    try {
      const data = await fetchJsonWithTimeout(
        `http://127.0.0.1:${port}/chat`,
        {
          method: "POST",
          headers: localHeaders({ "Content-Type": "application/json" }),
          credentials: "include",
          body: JSON.stringify({
            message: question,
            source: "browser-extension",
            stream: false,
          }),
        },
        RECALL_TIMEOUT_MS,
      );
      if (answerEl) answerEl.textContent = String(data.response || "").trim() || t(language, "recall.empty");
      setGrounding(groundingView(data, language));
    } catch (err) {
      if (answerEl) answerEl.textContent = "";
      // A failed recall is not an ungrounded answer — say what actually
      // happened instead of badging a verdict nobody issued.
      setStatus(
        t(language, "recall.failed", {
          message: err instanceof Error ? err.message : String(err),
        }),
        "err",
      );
    } finally {
      askBtn.disabled = false;
    }
  });

  // ── Pending approvals & server language sync ──────────────────────────────
  const approvalsEl = document.getElementById("approvals");
  (async () => {
    const port = normalizePort(portInput?.value);
    try {
      const featData = await fetchJsonWithTimeout(
        `http://127.0.0.1:${port}/api/features`,
        { credentials: "include", headers: localHeaders() },
      );
      const synced = extractServerLanguage(featData);
      if (synced && synced !== language) {
        applyLanguage(synced);
        await saveStorage({ latticeLanguage: synced });
      }
    } catch (err) {
      // Optional sync: popup keeps stored/browser language when runtime is offline.
      console.warn("Lattice AI extension: optional language sync skipped", err);
    }
    if (!approvalsEl) return;
    try {
      const approvalsData = await fetchJsonWithTimeout(
        `http://127.0.0.1:${port}/agent/approvals`,
        { credentials: "include", headers: localHeaders() },
      );
      approvalsEl.textContent = approvalsView(approvalsData, language);
    } catch (err) {
      // Non-fatal: an unreachable runtime is already reported by the capture path.
      console.warn("Lattice AI extension: approval count check failed", err);
    }
  })();
}

if (typeof document !== "undefined") initializePopup();

// Node's built-in test runner loads these pure helpers without a browser DOM.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
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
  };
}
