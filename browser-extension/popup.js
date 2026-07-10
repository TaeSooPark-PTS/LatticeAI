// Send to Lattice AI — popup logic.
// Captures the active tab's readable content and posts it to the LOCAL runtime
// (http://127.0.0.1:<port>/api/browser/ingest-current-tab). Nothing leaves the
// machine; there is no cloud endpoint anywhere in this extension.

const DEFAULT_PORT = 4825;
const REQUEST_TIMEOUT_MS = 30_000;

function normalizePort(value) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535
    ? parsed
    : DEFAULT_PORT;
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
}

if (typeof document !== "undefined") initializePopup();

// Node's built-in test runner loads these pure helpers without a browser DOM.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { DEFAULT_PORT, REQUEST_TIMEOUT_MS, fetchWithTimeout, normalizePort };
}
