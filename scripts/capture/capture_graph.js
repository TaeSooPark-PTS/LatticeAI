#!/usr/bin/env node
const { capturePage } = require("./capture_page");

capturePage({ path: "/graph", waitFor: "#graph", filename: "graph.png", settleMs: 1400 })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
