# Lattice AI v4.5.0 Model Experience Report

## Goal

Users should not need to understand local runtime internals to choose and load a model.

## Reset Applied

The model screen is now organized around:

Environment Analysis -> Recommended Models -> Install -> Download Progress -> Validate -> Load / Ready

## Basic Mode Changes

- runtime and loader details are hidden
- MLX/GGUF/Ollama-style implementation language is sanitized
- unavailable models show actionable readiness language
- model validation names are humanized
- compatible alternatives are shown without local runtime format jargon

## Advanced/Admin Behavior

Advanced/Admin modes keep diagnostic detail, including runtime labels and load IDs, so model routing issues can still be diagnosed without changing backend behavior.

## Regression Coverage

Playwright now proves:

- recommended model setup is visible
- Gemma models remain distinguished
- Basic mode does not expose `MLX`
- Basic mode does not expose `GGUF`
- missing Python/module noise is not shown
