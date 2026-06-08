// Send to Lattice AI — popup logic.
// Captures the active tab's readable content and posts it to the LOCAL runtime
// (http://127.0.0.1:<port>/api/browser/ingest-current-tab). Nothing leaves the
// machine; there is no cloud endpoint anywhere in this extension.

const portInput = document.getElementById("port");
const sendBtn = document.getElementById("send");
const statusEl = document.getElementById("status");

// Remember the last-used port.
chrome.storage?.local.get(["latticePort"], (r) => {
  if (r && r.latticePort) portInput.value = r.latticePort;
});

function setStatus(message, kind) {
  statusEl.textContent = message;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

// Runs in the page context to extract a sanitized capture payload.
function capturePage() {
  const clone = document.cloneNode(true);
  clone.querySelectorAll("script,style,noscript,template,svg").forEach((n) => n.remove());
  const text = (clone.body ? clone.body.innerText : document.title || "").trim();
  const selected = (window.getSelection && window.getSelection().toString()) || "";
  return {
    url: location.href,
    title: document.title || location.href,
    text: text.slice(0, 4 * 1024 * 1024),
    selected_text: selected.slice(0, 200000),
    captured_at: new Date().toISOString(),
  };
}

sendBtn.addEventListener("click", async () => {
  sendBtn.disabled = true;
  setStatus("Capturing…");
  const port = parseInt(portInput.value, 10) || 8000;
  chrome.storage?.local.set({ latticePort: port });

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) throw new Error("No active tab.");
    const [{ result: payload }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: capturePage,
    });

    setStatus("Sending to local Lattice AI…");
    const resp = await fetch(`http://127.0.0.1:${port}/api/browser/ingest-current-tab`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include", // reuse the local app session cookie if present
      body: JSON.stringify(payload),
    });

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
    setStatus(
      `Failed: ${err.message}. Is Lattice AI running locally and are you signed in?`,
      "err"
    );
  } finally {
    sendBtn.disabled = false;
  }
});
