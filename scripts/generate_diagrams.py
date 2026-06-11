#!/usr/bin/env python3
"""Generate Lattice AI documentation diagrams (PNG) + hero frames.

These are *structural diagrams* rendered from the live codebase — the
architecture layers, the onboarding flow, the real tri-state model
recommendation output, the real Knowledge Graph node/edge taxonomy, the
workspace/role model, and the skill-marketplace structure. They are deliberately
diagrams (not UI screenshots) so they stay accurate and reproducible in CI.

Run from the repo root:

    python scripts/generate_diagrams.py

Outputs to ``docs/images/`` (and frame PNGs to ``docs/images/tmp_frames/`` which
the hero GIF is assembled from via ffmpeg by the caller).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "images"
FRAMES = OUT / "tmp_frames"

# ── design system ─────────────────────────────────────────────────────────────
BG = (11, 17, 32)
PANEL = (23, 33, 58)
PANEL_LT = (32, 45, 74)
BORDER = (51, 65, 95)
TEXT = (226, 232, 240)
MUTED = (148, 163, 184)
ACCENT = (124, 92, 255)
ACCENT2 = (56, 189, 248)
GREEN = (34, 197, 94)
AMBER = (245, 158, 11)
GRAY = (107, 114, 128)

_F = "/System/Library/Fonts/Supplemental/Arial.ttf"
_FB = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
_FM = "/System/Library/Fonts/Menlo.ttc"


def font(size, bold=False, mono=False):
    try:
        return ImageFont.truetype(_FM if mono else (_FB if bold else _F), size)
    except Exception:
        return ImageFont.load_default()


def canvas(w, h, bg=BG):
    img = Image.new("RGB", (w, h), bg)
    return img, ImageDraw.Draw(img)


def rrect(d, box, r, fill=None, outline=None, width=2):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def measure(d, s, f):
    b = d.textbbox((0, 0), s, font=f)
    return b[2] - b[0], b[3] - b[1]


def text(d, xy, s, f, fill=TEXT, anchor="la"):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def ctext(d, cx, y, s, f, fill=TEXT):
    w, _ = measure(d, s, f)
    d.text((cx - w / 2, y), s, font=f, fill=fill)


def badge(d, x, y, label, color, fsize=15):
    f = font(fsize, bold=True)
    w, h = measure(d, label, f)
    pad = 9
    box = [x, y, x + w + pad * 2, y + h + 9]
    rrect(d, box, (h + 9) / 2, fill=color)
    text(d, (x + pad, y + 4), label, f, fill=(11, 17, 32) if color in (GREEN, AMBER) else (255, 255, 255))
    return box[2] - box[0]


def watermark(d, w, h):
    f = font(15, bold=True)
    text(d, (w - 200, h - 30), "Lattice AI", f, fill=MUTED)
    d.ellipse([w - 222, h - 27, w - 208, h - 13], fill=ACCENT)


def header(d, w, title, subtitle):
    text(d, (44, 34), title, font(34, bold=True), fill=TEXT)
    text(d, (44, 80), subtitle, font(18), fill=MUTED)
    d.line([(44, 116), (w - 44, 116)], fill=BORDER, width=2)


def save(img, name):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    img.save(p)
    print("wrote", p.relative_to(ROOT))


def wrap(d, s, f, max_w):
    words, lines, cur = s.split(), [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if measure(d, trial, f)[0] <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# ── 1. architecture ───────────────────────────────────────────────────────────
def diagram_architecture():
    w, h = 1680, 1080
    img, d = canvas(w, h)
    header(d, w, "Lattice AI — Architecture (v1.5.0)",
           "Thin compat entrypoint · modular FastAPI app · routers + services + core · local engines & knowledge graph")

    def band(y, bh, title, items, accent, item_color=PANEL_LT):
        rrect(d, [44, y, w - 44, y + bh], 14, fill=PANEL, outline=BORDER, width=2)
        text(d, (60, y + 12), title, font(17, bold=True), fill=accent)
        bx = 60
        by = y + 44
        fi = font(14, mono=True)
        for it in items:
            tw = measure(d, it, fi)[0] + 22
            if bx + tw > w - 60:
                bx = 60
                by += 38
            rrect(d, [bx, by, bx + tw, by + 28], 7, fill=item_color, outline=BORDER, width=1)
            text(d, (bx + 11, by + 6), it, fi, fill=TEXT)
            bx += tw + 10

    def arrow_down(cx, y1, y2):
        d.line([(cx, y1), (cx, y2)], fill=ACCENT2, width=3)
        d.polygon([(cx - 6, y2 - 9), (cx + 6, y2 - 9), (cx, y2)], fill=ACCENT2)

    band(140, 92, "CLIENTS",
         ["Web UI  (static/)", "VS Code / Cursor  (vscode-extension/)", "Telegram bot", "MCP clients", "PWA"],
         ACCENT2)
    arrow_down(w // 2, 232, 258)

    band(258, 78, "ENTRYPOINT",
         ["server.py  →  server:app  (thin compat)", "latticeai/server_app.py  (FastAPI assembly · lifespan · middleware · static mount · router wiring)"],
         ACCENT)
    arrow_down(w // 2, 336, 362)

    band(362, 130, "API ROUTERS   latticeai/api/",
         ["chat", "models", "workspace", "mcp", "admin", "auth", "tools", "computer_use",
          "local_files", "permissions", "garden", "setup", "static_routes", "health", "security_dashboard"],
         ACCENT2)
    arrow_down(w // 2, 492, 518)

    band(518, 112, "SERVICES   latticeai/services/",
         ["model_runtime", "model_catalog", "model_recommendation", "tool_dispatch", "upload_service",
          "chat_service", "workspace_service", "model_service", "app_context"],
         ACCENT)
    arrow_down(w // 2, 630, 656)

    band(656, 130, "CORE   latticeai/core/",
         ["workspace_os", "tool_registry", "agent", "enterprise", "enterprise_admin", "security",
          "sessions", "audit", "config", "model_compat", "model_resolution", "graph_curator"],
         ACCENT2)
    arrow_down(w // 2, 786, 812)

    # bottom: engines + graph + storage as three columns
    cols = [
        ("ENGINES   llm_router.py", ["MLX (Apple Silicon)", "Ollama", "vLLM", "llama.cpp", "LM Studio", "OpenAI-compatible"]),
        ("KNOWLEDGE GRAPH", ["knowledge_graph.py", "KGStore v2 (SQLite)", "Graph RAG", "graph_curator"]),
        ("STORAGE / MCP", ["~/.ltcai/  (local)", "~/.ltcai-brain/", "mcp_registry.py", "skills/"]),
    ]
    cw = (w - 44 * 2 - 24 * 2) // 3
    for i, (title, items) in enumerate(cols):
        x = 44 + i * (cw + 24)
        rrect(d, [x, 812, x + cw, 980], 14, fill=PANEL, outline=BORDER, width=2)
        text(d, (x + 16, 824), title, font(15, bold=True), fill=ACCENT)
        fy = 858
        fi = font(14, mono=True)
        for it in items:
            d.ellipse([x + 16, fy + 5, x + 24, fy + 13], fill=ACCENT2)
            text(d, (x + 32, fy), it, fi, fill=TEXT)
            fy += 28

    watermark(d, w, h)
    save(img, "architecture.png")


# ── 2. onboarding flow ──────────────────────────────────────────────────────--
def diagram_onboarding():
    w, h = 1680, 620
    img, d = canvas(w, h)
    header(d, w, "Onboarding Flow",
           "From install to first chat — system scan and model recommendation are built in")
    steps = [
        ("1", "Install", "pip install ltcai", ACCENT2),
        ("2", "System Scan", "OS · CPU · GPU · RAM · Disk", ACCENT),
        ("3", "Model Recommendation", "Recommended / Compatible / Not", GREEN),
        ("4", "Workspace", "Personal or Organization", ACCENT2),
        ("5", "Indexing", "approve local folders", ACCENT),
        ("6", "Knowledge Graph", "nodes & edges auto-built", ACCENT2),
        ("7", "First Chat", "graph-aware answers", GREEN),
    ]
    n = len(steps)
    margin = 44
    gap = 22
    bw = (w - margin * 2 - gap * (n - 1)) // n
    y = 200
    bh = 220
    for i, (num, title, sub, accent) in enumerate(steps):
        x = margin + i * (bw + gap)
        rrect(d, [x, y, x + bw, y + bh], 14, fill=PANEL, outline=BORDER, width=2)
        d.ellipse([x + bw / 2 - 22, y + 22, x + bw / 2 + 22, y + 66], fill=accent)
        ctext(d, x + bw / 2, y + 30, num, font(24, bold=True), fill=(11, 17, 32))
        for j, line in enumerate(wrap(d, title, font(17, bold=True), bw - 20)):
            ctext(d, x + bw / 2, y + 90 + j * 24, line, font(17, bold=True), fill=TEXT)
        for j, line in enumerate(wrap(d, sub, font(13), bw - 24)):
            ctext(d, x + bw / 2, y + 150 + j * 20, line, font(13), fill=MUTED)
        if i < n - 1:
            ax = x + bw + 4
            d.line([(ax, y + bh / 2), (ax + gap - 8, y + bh / 2)], fill=ACCENT2, width=3)
            d.polygon([(ax + gap - 12, y + bh / 2 - 6), (ax + gap - 12, y + bh / 2 + 6), (ax + gap - 2, y + bh / 2)], fill=ACCENT2)
    text(d, (margin, y + bh + 40),
         "Each step writes onboarding state via /workspace/onboarding/* so progress is resumable.",
         font(15), fill=MUTED)
    watermark(d, w, h)
    save(img, "onboarding.png")


# ── 3. model recommendation (live data) ─────────────────────────────────────--
def diagram_model_recommendation():
    from latticeai.services.model_recommendation import recommend_catalog, RECOMMENDED, COMPATIBLE
    # Representative machine: 32 GB Apple Silicon.
    profile = {"os": "darwin", "arch": "arm64", "ram_mb": 32 * 1024,
               "gpu": {"vendor": "apple", "vram_mb": 32 * 1024}}
    rec = recommend_catalog(profile, engine="local_mlx")

    w, h = 1680, 1020
    img, d = canvas(w, h)
    header(d, w, "Local Model Recommendation",
           "Hardware-aware tri-state classification — example: 32 GB Apple Silicon (MLX)")

    # legend
    lx = 44
    lx += badge(d, lx, 130, "Recommended", GREEN) + 14
    lx += badge(d, lx, 130, "Compatible", AMBER) + 14
    lx += badge(d, lx, 130, "Not Recommended", GRAY) + 14
    counts = rec["counts"]
    text(d, (w - 520, 134),
         f"recommended {counts['recommended']}  ·  compatible {counts['compatible']}  ·  not {counts['not_recommended']}",
         font(15, mono=True), fill=MUTED)

    # show top families per directive
    want = ["Gemma 4", "Qwen3-VL", "Llama 4"]
    fams = [f for f in rec["families"] if f["family"] in want][:6]
    y = 176
    fi = font(15)
    fm = font(13, mono=True)
    for fam in fams:
        models = fam["models"][:5]
        rh = 40 + len(models) * 30 + 16
        rrect(d, [44, y, w - 44, y + rh], 12, fill=PANEL, outline=BORDER, width=2)
        text(d, (60, y + 12), fam["family"], font(18, bold=True), fill=ACCENT2)
        best = fam.get("best")
        if best:
            badge(d, 60 + measure(d, fam["family"], font(18, bold=True))[0] + 18, y + 12,
                  "best: " + (best["name"] or ""), GREEN, fsize=13)
        ry = y + 46
        for m in models:
            color = {RECOMMENDED: GREEN, COMPATIBLE: AMBER}.get(m["status"], GRAY)
            d.ellipse([62, ry + 5, 74, ry + 17], fill=color)
            text(d, (86, ry), m["name"] or m["id"], fi, fill=TEXT)
            text(d, (w - 360, ry), str(m["size"] or ""), fm, fill=MUTED)
            badge(d, w - 230, ry - 2, {"recommended": "Recommended", "compatible": "Compatible"}.get(m["status"], "Not Rec."), color, fsize=12)
            ry += 30
        y += rh + 14

    watermark(d, w, h)
    save(img, "model-recommendation.png")


# ── 4. workspace model ──────────────────────────────────────────────────────--
def diagram_workspace():
    w, h = 1680, 760
    img, d = canvas(w, h)
    header(d, w, "Workspaces — Personal & Organization",
           "Switch instantly · workspace-scoped data · explicit active-workspace state")

    def col(x, title, accent, lines):
        cw = (w - 44 * 2 - 40) // 2
        rrect(d, [x, 150, x + cw, 600], 16, fill=PANEL, outline=accent, width=2)
        text(d, (x + 22, 168), title, font(22, bold=True), fill=accent)
        fy = 218
        for icon, t, sub in lines:
            d.ellipse([x + 22, fy + 4, x + 38, fy + 20], fill=accent)
            text(d, (x + 52, fy), t, font(16, bold=True), fill=TEXT)
            text(d, (x + 52, fy + 24), sub, font(13), fill=MUTED)
            fy += 62
        return cw

    cw = col(44, "Personal Workspace", ACCENT2, [
        ("", "Single owner", "no-auth local owner fallback"),
        ("", "Private data", "snapshots · memory · agents · traces"),
        ("", "Local-first", "stored under ~/.ltcai"),
        ("", "Instant default", "active on first run"),
    ])
    col(44 + cw + 40, "Organization Workspace", ACCENT, [
        ("", "Roles", "owner · admin · member · viewer"),
        ("", "Shared & scoped", "data carries workspace_id"),
        ("", "Membership", "invite, manage, archive (non-destructive)"),
        ("", "Visibility", "active workspace + role shown in header"),
    ])

    rrect(d, [44, 624, w - 44, 700], 12, fill=PANEL_LT, outline=BORDER, width=2)
    text(d, (60, 636), "Scoping", font(15, bold=True), fill=ACCENT2)
    text(d, (60, 662),
         "Reads/writes resolve scope via the  X-Workspace-Id  header → WorkspaceService gate "
         "(non-members blocked, viewers read-only, owners/admins manage).",
         font(14, mono=True), fill=TEXT)
    watermark(d, w, h)
    save(img, "workspace.png")


# ── 5. knowledge graph taxonomy (live data) ─────────────────────────────────--
def diagram_graph():
    import kg_schema as k
    nodes = [e.value.title().replace("_", " ") for e in k.NodeType][:18]
    edges = [e.value.lower() for e in k.EdgeType][:18]

    w, h = 1680, 820
    img, d = canvas(w, h)
    header(d, w, "Knowledge Graph — Taxonomy",
           f"{len(list(k.NodeType))} node types · {len(list(k.EdgeType))} edge types · auto-built from chats, files & folders")

    # center hub + sample relationship
    cx, cy = w // 2, 300
    rrect(d, [cx - 90, cy - 34, cx + 90, cy + 34], 16, fill=ACCENT, outline=None)
    ctext(d, cx, cy - 12, "Document", font(18, bold=True), fill=(255, 255, 255))
    sat = [("Concept", -360, -120, ACCENT2, "mentions"),
           ("Person", 360, -120, ACCENT2, "authored_by"),
           ("Chat", -360, 120, GREEN, "references"),
           ("Task", 360, 120, AMBER, "contains")]
    for label, dx, dy, color, rel in sat:
        sx, sy = cx + dx, cy + dy
        d.line([(cx, cy), (sx, sy)], fill=BORDER, width=2)
        mx, my = (cx + sx) / 2, (cy + sy) / 2
        text(d, (mx, my - 18), rel, font(12, mono=True), fill=MUTED, anchor="ma")
        rrect(d, [sx - 70, sy - 26, sx + 70, sy + 26], 12, fill=PANEL, outline=color, width=2)
        ctext(d, sx, sy - 9, label, font(15, bold=True), fill=color)

    # node / edge type chips
    def chips(y, title, items, color):
        text(d, (44, y), title, font(16, bold=True), fill=color)
        bx, by = 44, y + 30
        fi = font(13, mono=True)
        for it in items:
            tw = measure(d, it, fi)[0] + 20
            if bx + tw > w - 44:
                bx = 44
                by += 34
            rrect(d, [bx, by, bx + tw, by + 26], 7, fill=PANEL, outline=color, width=1)
            text(d, (bx + 10, by + 5), it, fi, fill=TEXT)
            bx += tw + 9
        return by + 40

    ny = chips(470, "Node types", nodes, ACCENT2)
    chips(ny + 6, "Edge types", edges, GREEN)
    watermark(d, w, h)
    save(img, "graph.png")


# ── 6. organization roles × permissions ─────────────────────────────────────--
def diagram_organization():
    w, h = 1680, 720
    img, d = canvas(w, h)
    header(d, w, "Organization — Roles & Permissions",
           "Member visibility, role clarity, and a transparent permission matrix")

    roles = ["owner", "admin", "member", "viewer"]
    perms = ["read", "write", "manage_members", "manage_workspace"]
    grant = {
        "owner": {"read", "write", "manage_members", "manage_workspace"},
        "admin": {"read", "write", "manage_members"},
        "member": {"read", "write"},
        "viewer": {"read"},
    }
    x0, y0 = 60, 180
    col_w, row_h = 360, 92
    # header row
    for j, p in enumerate(perms):
        cx = x0 + col_w + j * 300 + 150
        ctext(d, cx, y0 - 4, p, font(15, bold=True), fill=ACCENT2)
    for i, r in enumerate(roles):
        ry = y0 + 30 + i * row_h
        rrect(d, [x0, ry, x0 + col_w, ry + row_h - 16], 12, fill=PANEL, outline=ACCENT, width=2)
        text(d, (x0 + 18, ry + 16), r, font(20, bold=True), fill=TEXT)
        text(d, (x0 + 18, ry + 46), {"owner": "full control", "admin": "manage members",
              "member": "create & edit", "viewer": "read-only"}[r], font(13), fill=MUTED)
        for j, p in enumerate(perms):
            cx = x0 + col_w + j * 300 + 150
            cy = ry + (row_h - 16) / 2
            if p in grant[r]:
                d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=GREEN)
                ctext(d, cx, cy - 12, "✓", font(20, bold=True), fill=(11, 17, 32))
            else:
                d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], outline=GRAY, width=2)
    watermark(d, w, h)
    save(img, "organization.png")


# ── 7. skill marketplace ────────────────────────────────────────────────────--
def diagram_skills():
    w, h = 1680, 720
    img, d = canvas(w, h)
    header(d, w, "Skill Marketplace",
           "Browse, install, and keep skills current — Recommended · Popular · Installed · Updates")

    tabs = [("Recommended", ACCENT2), ("Popular", ACCENT), ("Installed", GREEN), ("Updates Available", AMBER)]
    tx = 44
    for label, color in tabs:
        tw = measure(d, label, font(16, bold=True))[0] + 36
        rrect(d, [tx, 150, tx + tw, 196], 10, fill=PANEL, outline=color, width=2)
        text(d, (tx + 18, 162), label, font(16, bold=True), fill=color)
        tx += tw + 14

    cards = [
        ("code-reviewer", "Recommended", ACCENT2, "Review diffs for bugs & risks"),
        ("docs-writer", "Popular", ACCENT, "Generate project documentation"),
        ("changelog-generator", "Installed", GREEN, "Changelog from git history"),
        ("security-review", "Updates Available", AMBER, "Scan code for vulnerabilities"),
        ("react-best-practices", "Recommended", ACCENT2, "React/Next performance"),
        ("deep-research", "Popular", ACCENT, "Multi-source cited research"),
    ]
    cw, ch, gap = 520, 150, 24
    for i, (name, tag, color, desc) in enumerate(cards):
        cxi = i % 3
        cyi = i // 3
        x = 44 + cxi * (cw + gap)
        y = 230 + cyi * (ch + gap)
        rrect(d, [x, y, x + cw, y + ch], 14, fill=PANEL, outline=BORDER, width=2)
        text(d, (x + 20, y + 18), name, font(19, bold=True), fill=TEXT)
        badge(d, x + 20, y + 54, tag, color, fsize=12)
        for j, line in enumerate(wrap(d, desc, font(14), cw - 40)):
            text(d, (x + 20, y + 92 + j * 20), line, font(14), fill=MUTED)
    text(d, (44, 230 + 2 * (ch + gap)),
         "Lifecycle: install · enable · disable · update · uninstall  (admin-gated, audited via /workspace/skills/*)",
         font(15), fill=MUTED)
    watermark(d, w, h)
    save(img, "skills.png")


# ── 8. hero frames (assembled into hero.gif by ffmpeg) ──────────────────────--
def hero_frames():
    FRAMES.mkdir(parents=True, exist_ok=True)
    stages = [
        ("Local files · chats · folders", "your workspace, indexed locally", ACCENT2),
        ("Automatic Knowledge Graph", "nodes & edges built from real work", ACCENT),
        ("Graph-aware chat & agents", "answers grounded in your memory", GREEN),
        ("One local AI Workspace OS", "private · local-first · yours", ACCENT2),
    ]
    w, h = 1280, 640
    for idx in range(len(stages)):
        img, d = canvas(w, h)
        # title
        ctext(d, w // 2, 60, "Lattice AI", font(40, bold=True), fill=TEXT)
        ctext(d, w // 2, 112, "AI Workspace OS for local-first graph, memory & agents", font(18), fill=MUTED)
        # pipeline dots
        n = len(stages)
        cxs = [w // 2 - 420, w // 2 - 140, w // 2 + 140, w // 2 + 420]
        labels = ["Files", "Graph", "Chat", "OS"]
        for i in range(n):
            active = i <= idx
            color = stages[i][2] if active else PANEL_LT
            r = 30 if active else 22
            d.ellipse([cxs[i] - r, 230 - r, cxs[i] + r, 230 + r], fill=color)
            ctext(d, cxs[i], 270, labels[i], font(15, bold=True), fill=TEXT if active else MUTED)
            if i < n - 1:
                lc = ACCENT2 if i < idx else BORDER
                d.line([(cxs[i] + 34, 230), (cxs[i + 1] - 34, 230)], fill=lc, width=4)
        # active stage card
        title, sub, color = stages[idx]
        rrect(d, [w // 2 - 460, 360, w // 2 + 460, 520], 18, fill=PANEL, outline=color, width=3)
        ctext(d, w // 2, 396, title, font(30, bold=True), fill=color)
        ctext(d, w // 2, 446, sub, font(18), fill=TEXT)
        watermark(d, w, h)
        p = FRAMES / f"frame_{idx:02d}.png"
        img.save(p)
        print("wrote", p.relative_to(ROOT))


def main():
    diagram_architecture()
    diagram_onboarding()
    diagram_model_recommendation()
    diagram_workspace()
    diagram_graph()
    diagram_organization()
    diagram_skills()
    hero_frames()


if __name__ == "__main__":
    main()
