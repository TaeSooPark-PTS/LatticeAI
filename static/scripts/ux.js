/* ============================================================================
 * Lattice AI — Shared UX runtime (v2.2.1)
 *
 * 모든 페이지에서 로드된다. 페이지마다 일부 요소가 없을 수 있으므로 전부
 * 방어적으로(존재 확인 후) 동작한다. 기존 chat.js / graph.js / admin.js 의
 * 함수를 재정의하지 않고, 새 전역 기능만 추가한다.
 *
 *   - 다크/라이트 테마 (localStorage + OS 선호 + 토글)
 *   - 모바일 키보드 inset (--kb-inset) : 입력창이 키보드에 안 가리게
 *   - Escape 로 열린 모달/오버레이 닫기
 *   - 브레이크포인트 넘으면 열린 드로어 자동 정리
 *   - 그래프/관리자 네비 드로어 토글
 * ========================================================================== */
(function () {
  "use strict";

  var root = document.documentElement;

  /* ---------- 1. 테마 ---------- */
  var THEME_KEY = "lt-theme";

  function systemPrefersDark() {
    return window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function storedTheme() {
    try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
  }

  function applyTheme(mode) {
    if (mode !== "dark" && mode !== "light") {
      mode = systemPrefersDark() ? "dark" : "light";
    }
    root.setAttribute("data-lt-theme", mode);
    return mode;
  }

  // 초기 적용 (FOUC 최소화를 위해 가능한 한 빨리)
  applyTheme(storedTheme());

  window.setTheme = function (mode) {
    var applied = applyTheme(mode);
    try { localStorage.setItem(THEME_KEY, applied); } catch (e) {}
    return applied;
  };

  window.toggleTheme = function () {
    var cur = root.getAttribute("data-lt-theme") === "dark" ? "dark" : "light";
    return window.setTheme(cur === "dark" ? "light" : "dark");
  };

  // 사용자가 명시 선택을 안 했으면 OS 변화 따라가기
  if (window.matchMedia) {
    try {
      window.matchMedia("(prefers-color-scheme: dark)")
        .addEventListener("change", function (e) {
          if (!storedTheme()) applyTheme(e.matches ? "dark" : "light");
        });
    } catch (e) { /* Safari < 14 */ }
  }

  /* ---------- 2. 모바일 키보드 inset ---------- */
  var vv = window.visualViewport;
  if (vv) {
    var updateKbInset = function () {
      // 레이아웃 뷰포트와 비주얼 뷰포트의 차이 = 키보드(또는 브라우저 UI) 높이
      var inset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      // 작은 값(브라우저 바 미세 변화)은 무시
      root.style.setProperty("--kb-inset", (inset > 80 ? inset : 0) + "px");
    };
    vv.addEventListener("resize", updateKbInset);
    vv.addEventListener("scroll", updateKbInset);
    // 입력에 포커스되면 화면 안으로 스크롤
    document.addEventListener("focusin", function (e) {
      var t = e.target;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT")) {
        setTimeout(function () {
          if (t.scrollIntoView) {
            try { t.scrollIntoView({ block: "center", behavior: "smooth" }); }
            catch (err) { t.scrollIntoView(); }
          }
        }, 250);
      }
    });
  }

  /* ---------- 3. 드로어 토글 (그래프 / 관리자) ---------- */
  function bodyHas(cls) { return document.body.classList.contains(cls); }

  window.toggleGraphNav = function () {
    document.body.classList.toggle("graph-nav-open");
  };
  window.closeGraphNav = function () {
    document.body.classList.remove("graph-nav-open");
  };
  window.toggleAdminRail = function () {
    document.body.classList.toggle("admin-rail-open");
  };
  window.closeAdminRail = function () {
    document.body.classList.remove("admin-rail-open");
  };

  /* ---------- 4. Escape 로 닫기 ---------- */
  var OVERLAY_SELECTORS = [
    ".acct-modal-overlay", ".mcp-modal-overlay", ".mode-modal-overlay",
    ".workspace-modal-overlay", ".advanced-settings-overlay", ".model-overlay",
    ".perm-overlay", ".onboarding-overlay", ".pipeline-overlay",
    ".admin-overlay", ".vpc-overlay", ".status-overlay", ".cu-overlay",
    ".file-create-overlay", ".file-editor-overlay", ".local-browser-overlay"
  ];

  function isVisible(el) {
    if (!el) return false;
    var s = window.getComputedStyle(el);
    return s.display !== "none" && s.visibility !== "hidden" && el.offsetParent !== null;
  }

  function closeTopOverlay() {
    // 1) 드로어 먼저
    if (bodyHas("sidebar-open")) { document.body.classList.remove("sidebar-open"); return true; }
    if (bodyHas("graph-nav-open")) { window.closeGraphNav(); return true; }
    if (bodyHas("admin-rail-open")) { window.closeAdminRail(); return true; }

    // 2) 보이는 오버레이를 위에서부터 닫기
    var overlays = document.querySelectorAll(OVERLAY_SELECTORS.join(","));
    for (var i = overlays.length - 1; i >= 0; i--) {
      var el = overlays[i];
      if (isVisible(el)) {
        // 닫기 버튼이 있으면 클릭, 없으면 직접 숨김
        var btn = el.querySelector(
          "[onclick*='close'],.modal-close,.mcp-modal-close,.admin-close,.mode-close,.acct-close"
        );
        if (btn && btn.click) { btn.click(); }
        else { el.style.display = "none"; }
        return true;
      }
    }
    return false;
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" || e.keyCode === 27) {
      if (closeTopOverlay()) { e.stopPropagation(); }
    }
  });

  /* ---------- 5. 브레이크포인트 넘으면 드로어 정리 ---------- */
  if (window.matchMedia) {
    var desktop = window.matchMedia("(min-width: 1025px)");
    var onDesktop = function (e) {
      if (e.matches) {
        document.body.classList.remove("sidebar-open", "graph-nav-open", "admin-rail-open");
      }
    };
    try { desktop.addEventListener("change", onDesktop); }
    catch (e2) { /* old Safari */ try { desktop.addListener(onDesktop); } catch (e3) {} }
  }

  /* ---------- 6. 새 드로어용 오버레이 백드롭 클릭 ---------- */
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t && t.classList && t.classList.contains("sidebar-overlay")) {
      document.body.classList.remove("graph-nav-open", "admin-rail-open");
    }
  });
})();
