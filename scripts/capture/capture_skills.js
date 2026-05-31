#!/usr/bin/env node
const { capturePage } = require("./capture_page");

capturePage({ path: "/workspace#skills", waitFor: "#skill-list", filename: "skills.png" })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
