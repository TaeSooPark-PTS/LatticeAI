# v4.5.1 Onboarding Report

## Goal

Onboarding should make the next action obvious to a first-time, non-technical
user without documentation.

## Reimagined Journey

- Make it yours: account/profile setup.
- Choose a space: personal or organization workspace selection.
- Meet your Mac: environment analysis.
- Pick a brain: model recommendation.
- Install locally: consent-gated model/runtime setup.
- Try a question: model validation through conversation.
- Set the pace: Calm, Deep, or Admin mode.
- Explore memory: open the knowledge map.

## Behavior

The journey reads real backend state from profile, workspace, models, and model
recommendation APIs. The guide can be hidden through local storage, but no
backend state is faked to mark setup complete.

## Evidence

- Primary component: `frontend/src/components/FirstRunGuide.tsx`
- Desktop screenshot: `output/audits/v4.5.1-reimagining/screenshots/home-desktop.png`
- Walkthrough GIF: `output/audits/v4.5.1-reimagining/gifs/v4.5.1-reimagining-walkthrough.gif`
