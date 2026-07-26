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
function groundingView(payload) {
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
      text: cited.length ? `근거 있음 — ${cited.slice(0, 3).join(", ")}` : "근거 있음",
    };
  }
  if (status === "unsupported" || status === "no_context") {
    const reason = grounding && typeof grounding.reason === "string" ? grounding.reason.trim() : "";
    return { status, kind: "none", text: reason ? `근거 없음 — ${reason}` : "근거 없음" };
  }
  return { status: status || "unknown", kind: "unknown", text: "근거 확인 불가" };
}

/** Pending-approval summary line, or "" when nothing is waiting. */
function approvalsView(payload) {
  const pending =
    payload && typeof payload === "object" && Array.isArray(payload.pending)
      ? payload.pending.filter((item) => item && typeof item === "object" && item.run_id)
      : [];
  if (!pending.length) return "";
  return `⏳ 승인 대기 중인 실행 ${pending.length}건 — 웹 앱에서 승인하세요`;
}

async function fetchWithTimeout(url, options, timeoutMs = REQUEST_TIMEOUT_MS, fetchImpl = fetch) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchImpl(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
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

  // Remember the last-used valid port.
  chrome.storage?.local.get(["latticePort"], (result) => {
    portInput.value = String(normalizePort(result?.latticePort));
  });

  sendBtn.addEventListener("click", async () => {
    sendBtn.disabled = true;
    setStatus("Capturing…");
    const port = normalizePort(portInput.value);
    portInput.value = String(port);
    chrome.storage?.local.set({ latticePort: port });

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id) throw new Error("No active tab.");
      const [{ result: payload } = {}] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["capture-page.js"],
      });
      if (!payload || typeof payload !== "object") {
        throw new Error("The page did not return readable content.");
      }

      setStatus("Sending to local Lattice AI…");
      const resp = await fetchWithTimeout(
        `http://127.0.0.1:${port}/api/browser/ingest-current-tab`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include", // reuse the local app session cookie if present
          body: JSON.stringify(payload),
        },
      );

      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${detail.slice(0, 200)}`);
      }
      const data = await resp.json();
      if (data.status === "ok") {
        setStatus(data.duplicate ? "Already in your graph ✓" : "Added to Knowledge Graph ✓", "ok");
      } else {
        setStatus(`Not added: ${data.status} ${data.detail || ""}`, "err");
      }
    } catch (err) {
      const timedOut = err && err.name === "AbortError";
      const message = timedOut
        ? "The local Lattice AI request timed out after 30 seconds"
        : err instanceof Error
          ? err.message
          : String(err);
      setStatus(
        `Failed: ${message}. Is Lattice AI running locally and are you signed in?`,
        "err",
      );
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
    if (answerEl) answerEl.textContent = "Asking your Brain…";
    setGrounding(null);
    const port = normalizePort(portInput.value);
    try {
      const resp = await fetchWithTimeout(
        `http://127.0.0.1:${port}/chat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            message: question,
            source: "browser-extension",
            stream: false,
          }),
        },
        RECALL_TIMEOUT_MS,
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (answerEl) answerEl.textContent = String(data.response || "").trim() || "(빈 응답)";
      setGrounding(groundingView(data));
    } catch (err) {
      if (answerEl) answerEl.textContent = "";
      // A failed recall is not an ungrounded answer — say what actually
      // happened instead of badging a verdict nobody issued.
      setStatus(
        `Recall failed: ${err instanceof Error ? err.message : String(err)}. Is Lattice AI running and are you signed in?`,
        "err",
      );
    } finally {
      askBtn.disabled = false;
    }
  });

  // ── Pending approvals (v9.9.7) ────────────────────────────────────────────
  const approvalsEl = document.getElementById("approvals");
  (async () => {
    if (!approvalsEl) return;
    const port = normalizePort(portInput.value);
    try {
      const resp = await fetchWithTimeout(
        `http://127.0.0.1:${port}/agent/approvals`,
        { credentials: "include" },
      );
      if (!resp.ok) return;
      approvalsEl.textContent = approvalsView(await resp.json());
    } catch {
      // Silent: an unreachable runtime is already reported by the capture path.
    }
  })();
}

if (typeof document !== "undefined") initializePopup();

// Node's built-in test runner loads these pure helpers without a browser DOM.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    DEFAULT_PORT,
    REQUEST_TIMEOUT_MS,
    RECALL_TIMEOUT_MS,
    approvalsView,
    fetchWithTimeout,
    groundingView,
    normalizePort,
  };
}
