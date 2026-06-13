const { test, expect } = require("@playwright/test");

const ROUTES = [
  "brain", "knowledge-graph", "hybrid-search", "memory",
  "ask", "chat",
  "capture", "files", "pipeline", "my-computer",
  "act", "agents", "runs", "workflows", "hooks", "tools",
  "library", "models", "skills", "mcp", "marketplace",
  "system", "account", "workspace-admin", "snapshots", "activity", "network",
  "settings", "admin/users", "admin/permissions", "admin/audit", "admin/security",
  "admin/policies", "admin/private-vpc",
];

function trackPageErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e.message || e)));
  return errors;
}

test("React desktop shell boots with the reimagined navigation dock", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.goto("/app");
  await page.waitForSelector("text=Digital Brain");
  const nav = page.getByRole("navigation", { name: "Primary navigation" });
  for (const label of ["Home", "Ask", "Add", "Automate", "Library", "Care"]) {
    await expect(nav.getByRole("button", { name: label })).toBeVisible();
  }
  await expect(page.locator("body")).not.toContainText("v4.1.0 Release Candidate");
  await expect(page.locator("body")).toContainText(/v\d+\.\d+\.\d+/);
  expect(errors).toEqual([]);
});

test("first-run journey explains the product without documentation", async ({ page }) => {
  await page.goto("/app");
  await expect(page.locator("body")).toContainText("First 10 minutes");
  for (const label of [
    "Make it yours",
    "Choose a space",
    "Meet your Mac",
    "Pick a brain",
    "Install locally",
    "Try a question",
    "Set the pace",
    "Explore memory",
  ]) {
    await expect(page.locator("body")).toContainText(label);
  }
  await expect(page.getByRole("button", { name: "Set up model" })).toBeVisible();
});

test("old hash routes resolve into the replacement SPA without JS errors", async ({ page }) => {
  for (const route of ROUTES) {
    const errors = trackPageErrors(page);
    await page.goto(`/app#/${route}`);
    await page.waitForSelector("main h1, main h2", { timeout: 10000 });
    expect(errors, `${route} threw`).toEqual([]);
  }
});

test("knowledge graph renders a Cytoscape canvas and provenance coverage", async ({ page }) => {
  await page.goto("/app#/knowledge-graph");
  await page.waitForSelector("[data-testid='brain-cytoscape']");
  await expect(page.locator("body")).toContainText("Source coverage");
  await expect(page.locator("[data-testid='brain-cytoscape'] canvas").first()).toBeVisible();
});

test("offline startup loads local assets and shows honest unavailable state", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const localAsset = url.pathname === "/app"
      || url.pathname.startsWith("/static/")
      || url.pathname === "/manifest.json"
      || url.pathname === "/favicon.ico"
      || url.pathname === "/sw.js";
    if (localAsset) {
      await route.continue();
      return;
    }
    await route.abort();
  });
  await page.goto("/app#/brain");
  await page.waitForSelector("text=Digital Brain");
  await expect(page.locator("body")).toContainText("Starting");
  await expect(page.getByRole("navigation", { name: "Primary navigation" }).getByRole("button", { name: "Home" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("hybrid search calls the API and renders returned records", async ({ page }) => {
  await page.goto("/app#/hybrid-search");
  await page.getByPlaceholder("Search memories, graph nodes, and indexed documents").fill("retrieval");
  await page.locator("section").getByRole("button", { name: "Search" }).click();
  await expect(page.locator("body")).toContainText("Lattice AI");
});

test("Ask streams chat and shows context trace", async ({ page }) => {
  await page.goto("/app#/chat");
  await page.getByPlaceholder("Ask anything about your work...").fill("How does hybrid search rank results?");
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.locator("body")).toContainText("Hybrid retrieval");
  await expect(page.locator("body")).toContainText("Sources");
});

test("Capture exposes upload, local folder, URL, and processing controls", async ({ page }) => {
  await page.goto("/app#/files");
  await expect(page.locator("body")).toContainText("retrieval-design.pdf");
  await page.goto("/app#/my-computer");
  await expect(page.locator("body")).toContainText("Folder access");
  await page.goto("/app#/capture");
  await page.getByRole("button", { name: "Web" }).click();
  await expect(page.locator("body")).toContainText("Capture URL");
  await page.goto("/app#/pipeline");
  await expect(page.locator("body")).toContainText("Rebuild retrieval index");
});

test("Act surfaces agents, runs, workflow graph, triggers, hooks, and tools", async ({ page }) => {
  await page.goto("/app#/agents");
  await expect(page.locator("body")).toContainText("Planner");
  await page.goto("/app#/runs");
  await expect(page.locator("body")).toContainText("Approval inbox");
  await page.goto("/app#/workflows");
  await expect(page.locator(".react-flow")).toBeVisible();
  await expect(page.locator("body")).toContainText("Automation triggers");
  await page.goto("/app#/hooks");
  await expect(page.locator("body")).toContainText("Redact Secrets");
  await page.goto("/app#/tools");
  await expect(page.locator("body")).toContainText("Write File");
});

test("Library renders models, skills, tool connections, and marketplace registries", async ({ page }) => {
  await page.goto("/app#/models");
  await expect(page.locator("body")).toContainText("Qwen3-VL 8B");
  await expect(page.locator("body")).toContainText("Analyze this Mac, recommend a model, install only with consent, validate it, then load it.");
  await expect(page.locator("body")).toContainText("Needs attention before loading");
  await expect(page.locator("body")).not.toContainText("MLX");
  await expect(page.locator("body")).not.toContainText("GGUF");
  await expect(page.locator("body")).toContainText("Gemma 4 26B A4B Instruct");
  await expect(page.locator("body")).toContainText("Qwen3-VL 8B");
  await expect(page.locator("body")).not.toContainText("No module named");
  await page.goto("/app#/skills");
  await expect(page.locator("body")).toContainText("visual_regression");
  await page.goto("/app#/mcp");
  await expect(page.locator("body")).toContainText("Read File");
  await page.goto("/app#/marketplace");
  await expect(page.locator("body")).toContainText("Research Assistant");
});

test("Basic graph view hides developer endpoint leakage", async ({ page }) => {
  await page.goto("/app#/knowledge-graph");
  await page.waitForSelector("[data-testid='brain-cytoscape']");
  await expect(page.locator("body")).toContainText("Search, focus, and filter the ideas Lattice has learned from your workspace.");
  await expect(page.locator("body")).not.toContainText("/knowledge-graph/graph");
  await expect(page.locator("body")).not.toContainText("Cytoscape.js");
});

test("System renders account, workspaces, snapshots, activity, network, settings, and admin", async ({ page }) => {
  await page.goto("/app#/account");
  await expect(page.locator("body")).toContainText("admin@example.com");
  await page.goto("/app#/workspace-admin");
  await expect(page.locator("body")).toContainText("Design Org");
  await page.goto("/app#/snapshots");
  await expect(page.locator("body")).toContainText("v4 checkpoint");
  await page.goto("/app#/activity");
  await expect(page.locator("body")).toContainText("Workflow Started");
  await page.goto("/app#/network");
  await expect(page.locator("body")).toContainText("This Mac");
  await page.goto("/app#/settings");
  await expect(page.locator("body")).toContainText("Computer memory");
  await page.goto("/app#/admin/security");
  await expect(page.locator("body")).toContainText("Admin controls");
  await page.getByLabel("Experience mode").getByRole("button", { name: "Admin" }).click();
  await expect(page.locator("body")).toContainText("Security overview");
});

test("legacy page URLs redirect into the replacement app", async ({ page }) => {
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/app#\/chat$/);
  await page.goto("/workspace");
  await expect(page).toHaveURL(/\/app#\/workspace-admin$/);
  await page.goto("/graph");
  await expect(page).toHaveURL(/\/app#\/knowledge-graph$/);
});

test("mobile layout has no horizontal overflow and nav opens", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto("/app#/brain");
  await page.waitForSelector("main h1");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.getByLabel("Toggle theme").waitFor();
  await page.getByRole("button").first().click();
  await expect(page.getByText("Choose a room")).toBeVisible();
});
