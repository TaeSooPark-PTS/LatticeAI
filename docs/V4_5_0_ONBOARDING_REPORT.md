# v4.5.0 Onboarding Report

Date: 2026-06-13

## Restored Surface

The desktop shell now shows a first-run setup guide unless the user dismisses it
locally. It is also discoverable from the command palette as First-run Setup.

## Covered Steps

- Login: opens System -> Account.
- Workspace Selection: opens System -> Workspaces.
- Environment Analysis: opens Library -> Models and uses model recommendation
  data from `/models/recommendations`.
- Model Recommendation: surfaces the top recommended local model.
- Model Installation: keeps runtime install/model download consent visible.
- Model Validation: shows compatibility profile results after load.
- Mode Selection: links to System -> Settings and preserves Basic/Advanced/Admin.
- Brain Usage: opens the graph-first Brain surface.

## Privacy Behavior

No external model download, runtime install, or cloud call starts from
onboarding. Downloads and installs require the visible Library consent checkbox.

## Evidence

Primary screenshot: `output/audits/v4.5.0-rc/screenshots/01-first-run-setup.png`

Walkthrough GIF: `output/audits/v4.5.0-rc/gifs/v4.5.0-first-run-walkthrough.gif`
