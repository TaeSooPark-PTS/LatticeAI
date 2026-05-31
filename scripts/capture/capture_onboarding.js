#!/usr/bin/env node
const { capturePage } = require("./capture_page");

capturePage({ path: "/onboarding", waitFor: "#onboarding-steps", filename: "onboarding.png" })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
