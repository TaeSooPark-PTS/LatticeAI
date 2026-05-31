---
name: hello_skill
description: A minimal example skill bundled by the hello-world plugin to show that plugins extend (not replace) the existing skill system.
---

# Hello Skill

This skill is contributed by the `hello-world` plugin. When the plugin is
installed, the Plugin SDK registers this skill into the existing Workspace skill
registry via the same `mark_skill_installed` path used by standalone skills —
demonstrating that plugins are an additive layer on top of skills.

## Usage

Ask Lattice AI to greet a workspace member, or invoke the plugin action `greet`.
