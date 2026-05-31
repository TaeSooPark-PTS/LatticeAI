#!/usr/bin/env node
const { capturePage } = require("./capture_page");

capturePage({ path: "/admin#enterprise", waitFor: "#enterprise-capability-status", filename: "enterprise.png" })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
