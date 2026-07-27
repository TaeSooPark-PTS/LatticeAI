/**
 * Progressive-enhancement Network Boundary panel (hybrid Phase 3).
 *
 * The main React bundle is prebuilt; this module can be loaded alongside it
 * to offer a local-only / cloud toggle + transparency preview without waiting
 * for a full frontend rebuild.
 *
 * Usage (after auth cookie is present):
 *   <script src="/static/app/network-boundary-panel.js" defer></script>
 *   <div id="lattice-network-boundary-root"></div>
 */
(function () {
  "use strict";

  var ROOT_ID = "lattice-network-boundary-root";
  var API = "/api/network-boundary";

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "onclick") node.onclick = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c) node.appendChild(c);
    });
    return node;
  }

  function fetchJSON(url, opts) {
    return fetch(url, Object.assign({ credentials: "same-origin" }, opts || {})).then(
      function (r) {
        if (!r.ok) return r.json().then(function (j) {
          throw new Error((j && j.detail) || r.statusText);
        });
        return r.json();
      }
    );
  }

  function render(root, state) {
    root.innerHTML = "";
    var mode = state.mode || "local_only";
    var allows = !!state.allows_cloud;

    var title = el("div", {
      style:
        "font: 600 13px/1.4 system-ui,sans-serif;margin-bottom:6px;color:inherit;",
      text: "네트워크 경계 · Network Boundary",
    });

    var status = el("div", {
      style: "font: 12px/1.4 system-ui,sans-serif;opacity:0.85;margin-bottom:8px;",
      text: allows
        ? "클라우드 스트리밍 허용 중 (최소 관련 노드만 전송)"
        : "로컬만 — 이 컴퓨터를 벗어나지 않습니다",
    });

    var toggleBtn = el("button", {
      type: "button",
      style:
        "font: 12px system-ui,sans-serif;padding:6px 10px;border-radius:8px;" +
        "border:1px solid rgba(127,127,127,0.4);cursor:pointer;margin-right:8px;",
      text: allows ? "로컬만으로 전환" : "클라우드 스트리밍 허용",
      onclick: function () {
        var next = allows ? "local_only" : "cloud_allowed";
        var body = { mode: next, acknowledge_risk: next === "cloud_allowed" };
        fetchJSON(API, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
          .then(function () {
            return fetchJSON(API + "/ui-state");
          })
          .then(function (s) {
            render(root, s);
          })
          .catch(function (err) {
            alert(String(err.message || err));
          });
      },
    });

    var previewBox = el("div", {
      style:
        "margin-top:10px;font:11px/1.45 system-ui,sans-serif;" +
        "padding:8px;border-radius:8px;background:rgba(127,127,127,0.08);" +
        "max-height:160px;overflow:auto;display:none;",
    });

    var previewBtn = el("button", {
      type: "button",
      style:
        "font: 12px system-ui,sans-serif;padding:6px 10px;border-radius:8px;" +
        "border:1px solid rgba(127,127,127,0.4);cursor:pointer;",
      text: "전송 미리보기",
      onclick: function () {
        var msg =
          window.prompt("미리볼 메시지를 입력하세요", "") || "";
        if (!msg.trim()) return;
        fetchJSON(API + "/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg, top_k: 6 }),
        })
          .then(function (p) {
            previewBox.style.display = "block";
            previewBox.textContent =
              "mode: " +
              p.mode +
              "\nnodes: " +
              (p.titles || []).join(", ") +
              "\ntokens≈ " +
              p.token_estimate +
              (p.would_block ? "\nblocked: " + p.would_block : "") +
              "\n\n" +
              (p.compact_preview || "");
          })
          .catch(function (err) {
            alert(String(err.message || err));
          });
      },
    });

    var policy = state.policy || {};
    var policyLine = el("div", {
      style: "font:11px/1.4 system-ui,sans-serif;opacity:0.75;margin-top:8px;",
      text:
        "auto_commit=" +
        !!policy.auto_commit +
        " · multimodal=" +
        !!policy.allow_multimodal +
        " · blocked_types=" +
        ((policy.blocked_node_types || []).length || 0),
    });

    root.appendChild(title);
    root.appendChild(status);
    root.appendChild(toggleBtn);
    root.appendChild(previewBtn);
    root.appendChild(previewBox);
    root.appendChild(policyLine);
  }

  function boot() {
    var root = document.getElementById(ROOT_ID);
    if (!root) return;
    fetchJSON(API + "/ui-state")
      .then(function (state) {
        render(root, state);
      })
      .catch(function () {
        /* not signed in or API not mounted — stay silent */
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
