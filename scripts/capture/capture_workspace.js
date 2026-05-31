#!/usr/bin/env node
const { capturePage } = require("./capture_page");

capturePage({ path: "/workspace", waitFor: "#workspace-health-grid", filename: "workspace.png" })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
