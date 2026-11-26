const WIDTHS = [
  [1024, 768],
  [1280, 800],
  [1440, 920],
  [1920, 1080],
  [390, 844],
];

const ROUTES = [
  "brain",
  "capture",
  "hybrid-search",
  "knowledge-graph",
  "chronicle",
  "review",
  "runs",
  "models",
  "settings",
  "pipeline",
  "admin/users",
];

function collectViolations() {
  const violations = [];
  const root = document.documentElement;
  const width = window.innerWidth;
  const height = window.innerHeight;
  if (root.scrollWidth - root.clientWidth > 1) violations.push(`outer horizontal scroll ${root.scrollWidth - root.clientWidth}px`);
  if (width > 760 && document.querySelector(".brain-space .brain-shell-page") && root.scrollHeight - root.clientHeight > 1) {
    violations.push(`outer vertical scroll ${root.scrollHeight - root.clientHeight}px`);
  }
  const name = (element) => {
    const text = (element.getAttribute("aria-label") || element.textContent || element.getAttribute("placeholder") || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return `${element.tagName.toLowerCase()}.${String(element.getAttribute("class") || "").split(" ")[0]} "${text}"`;
  };
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    if (element.closest(".sr-only, .brain-skip-link")) return false;
    return element.checkVisibility({ opacityProperty: true, visibilityProperty: true });
  };
  const labels = document.querySelectorAll(
    "button, a, [role=tab], select, summary, .brain-local-badge, .page-title, h1, h2, h3, label, th, [class*=badge], [class*=pill]",
  );
  for (const element of labels) {
    if (!visible(element)) continue;
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent.trim();
      if (!text) continue;
      if (node.parentElement.getBoundingClientRect().width <= 1) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rects = Array.from(range.getClientRects()).filter((rect) => rect.width > 0);
      const lines = new Set(rects.map((rect) => Math.round(rect.top / 4))).size;
      if (lines < 2) continue;
      const shortLabel = text.length <= 14 && !element.matches("h1, h2, h3");
      if (shortLabel || text.length / lines < 8) violations.push(`wrapped ${name(element)} into ${lines} lines`);
    }
  }
  const controls = document.querySelectorAll("button, a[href], input, select, textarea, [role=tab], summary");
  for (const element of controls) {
    if (!visible(element)) continue;
    if (element.matches("input[type=file], .brain-organism")) continue;
    const rect = element.getBoundingClientRect();
    let scrollX = false;
    let scrollY = false;
    for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      const box = parent.getBoundingClientRect();
      if (/auto|scroll/.test(style.overflowX)) scrollX = true;
      if (/auto|scroll/.test(style.overflowY)) scrollY = true;
      if (!scrollX && /hidden|clip/.test(style.overflowX) && (rect.right > box.right + 1 || rect.left < box.left - 1)) {
        violations.push(`clipped-x ${name(element)} by ${name(parent)}`);
        break;
      }
      if (!scrollY && /hidden|clip/.test(style.overflowY) && (rect.bottom > box.bottom + 1 || rect.top < box.top - 1)) {
        violations.push(`clipped-y ${name(element)} by ${name(parent)}`);
        break;
      }
    }
    if (!scrollX && (rect.right > width + 1 || rect.left < -1)) violations.push(`offscreen-x ${name(element)}`);
    if (getComputedStyle(element).whiteSpace === "nowrap" && element.scrollWidth > element.clientWidth + 2 && element.matches("button, a")) {
      violations.push(`truncated ${name(element)}`);
    }
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    if (cx <= 0 || cy <= 0 || cx >= width || cy >= height || element.disabled) continue;
    let hiddenByScroll = false;
    for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (!/auto|scroll|hidden|clip/.test(style.overflowX + style.overflowY)) continue;
      const box = parent.getBoundingClientRect();
      if (cx < box.left || cx > box.right || cy < box.top || cy > box.bottom) hiddenByScroll = true;
    }
    if (hiddenByScroll) continue;
    const hit = document.elementFromPoint(cx, cy);
    if (!hit || element === hit || element.contains(hit) || hit.contains(element)) continue;
    if (hit.closest("label") && hit.closest("label").contains(element)) continue;
    if (hit.closest(".brain-mobile-nav, .brain-topbar") && !element.closest(".brain-mobile-nav, .brain-topbar")) continue;
    violations.push(`covered ${name(element)} by ${name(hit)}`);
  }
  for (const handle of document.querySelectorAll(".chronicle-growth-handle")) {
    const rect = handle.getBoundingClientRect();
    const box = handle.closest(".chronicle-growth-plot").getBoundingClientRect();
    if (rect.right > box.right + 1 || rect.left < box.left - 1) violations.push("clipped chronicle growth handle");
  }
  return Array.from(new Set(violations));
}

module.exports = { WIDTHS, ROUTES, collectViolations };
