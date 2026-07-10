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
  DEFAULT_PORT,
  REQUEST_TIMEOUT_MS,
  fetchWithTimeout,
  normalizePort,
} = require("../browser-extension/popup.js");

function runCapture(text, selectedText = "") {
  const context = {
    Date,
    TextDecoder,
    TextEncoder,
    document: {
      title: "Unicode capture",
      cloneNode() {
        return {
          body: { innerText: text },
          querySelectorAll() {
            return [];
          },
        };
      },
    },
    location: { href: "https://example.test/page" },
    window: {
      getSelection() {
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
