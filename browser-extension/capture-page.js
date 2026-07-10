// Executed in the active tab's isolated extension world.
// The final expression is returned by chrome.scripting.executeScript.
(() => {
  const MAX_TEXT_BYTES = 4 * 1024 * 1024;

  function truncateUtf8(value, maxBytes) {
    const text = String(value || "");
    const encoded = new TextEncoder().encode(text);
    if (encoded.byteLength <= maxBytes) return text;

    // Streaming decode keeps an incomplete trailing code point buffered instead
    // of emitting U+FFFD, so re-encoding cannot exceed the backend byte limit.
    return new TextDecoder("utf-8").decode(encoded.subarray(0, maxBytes), {
      stream: true,
    });
  }

  const clone = document.cloneNode(true);
  clone
    .querySelectorAll("script,style,noscript,template,svg")
    .forEach((node) => node.remove());
  const rawText = (clone.body ? clone.body.innerText : document.title || "").trim();
  const selected = (window.getSelection && window.getSelection().toString()) || "";
  return {
    url: location.href,
    title: document.title || location.href,
    text: truncateUtf8(rawText, MAX_TEXT_BYTES),
    selected_text: selected.slice(0, 200000),
    captured_at: new Date().toISOString(),
  };
})();
