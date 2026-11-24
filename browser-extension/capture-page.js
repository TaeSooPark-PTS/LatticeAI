// Executed in the active tab's isolated extension world.
// The final expression is returned by chrome.scripting.executeScript.
(() => {
  const MAX_TEXT_BYTES = 4 * 1024 * 1024;
  const MAX_SELECTED_BYTES = 200_000;
  const SKIPPED_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG"]);

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

  function isElementHidden(el) {
    if (!el || typeof el !== "object") return false;
    if (el.hidden) return true;
    if (typeof el.getAttribute === "function" && el.getAttribute("aria-hidden") === "true") {
      return true;
    }
    if (typeof window.getComputedStyle === "function") {
      const style = window.getComputedStyle(el);
      if (style) {
        if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") {
          return true;
        }
        // Common screen-reader-only / visually-hidden CSS clipping pattern
        if (
          (style.width === "1px" && style.height === "1px" && style.overflow === "hidden") ||
          style.clip === "rect(0px, 0px, 0px, 0px)" ||
          style.clipPath === "inset(50%)"
        ) {
          return true;
        }
      }
    }
    return false;
  }

  function extractVisibleText() {
    const body = document.body;
    if (!body) return String(document.title || "").trim();

    // Walk the live rendered DOM so CSS layout / visibility are respected
    // without cloning into a detached document (where innerText degrades to textContent).
    if (typeof document.createTreeWalker === "function" && typeof NodeFilter !== "undefined") {
      const parts = [];
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const value = String(node.nodeValue || "").trim();
        if (!value) continue;
        let ancestor = node.parentElement;
        let skip = false;
        while (ancestor && ancestor !== body.parentElement) {
          const tag = String(ancestor.tagName || "").toUpperCase();
          if (SKIPPED_TAGS.has(tag) || isElementHidden(ancestor)) {
            skip = true;
            break;
          }
          ancestor = ancestor.parentElement;
        }
        if (!skip) parts.push(value);
      }
      return parts.join("\n").trim();
    }

    // Fallback to live document.body.innerText (never a detached clone).
    return String(body.innerText || document.title || "").trim();
  }

  const rawText = extractVisibleText();
  const selection = typeof window.getSelection === "function" ? window.getSelection() : null;
  const selected = selection ? selection.toString() : "";
  return {
    url: location.href,
    title: document.title || location.href,
    text: truncateUtf8(rawText, MAX_TEXT_BYTES),
    selected_text: truncateUtf8(selected, MAX_SELECTED_BYTES),
    captured_at: new Date().toISOString(),
  };
})();
