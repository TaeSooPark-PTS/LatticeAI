# Lattice AI – UX Simplification & Brain Emphasis Review

**Date**: 2026-06-22
**Focus**: How to make the interface feel dramatically simpler and more intuitive while making the "Brain" the unmistakable emotional and visual center of the experience.

## Current State Assessment

### Strengths
- Strong "Living Brain" language already exists in README and onboarding copy.
- Clear multi-level depth model (Level 1 Now memory → Level 5 Full graph).
- Consent-first, non-overwhelming model selection.
- Separate Admin surface (good separation of concerns).

### Current UX Friction Points
- Onboarding still feels like "install a model then chat" rather than "wake up your Brain".
- Brain Chat Home looks like a conventional chat interface with side panels.
- "Brain" is mentioned in text but not strongly represented visually as a persistent entity.
- Users must actively navigate to "Depths" (memory/topics/relationships/graph). The Brain does not feel alive by default.
- Environment Analysis + Model recommendation steps are necessary but presented linearly, increasing perceived complexity.
- Graph and advanced features appear as separate destinations instead of natural extensions of the Brain.

## Core Design Direction: "The Brain Is The Product"

**Guiding Principle**:
The user should feel they are *inside* or *talking directly to* their Brain at all times, not using an app that happens to have a brain feature.

### Key Shifts
1. **From "Chat + Sidebar" → "Living Brain Surface"**
2. **From "Navigate to depths" → "The Brain naturally reveals depth"**
3. **From "Model selection as a step" → "Model is the voice the Brain uses"**
4. **Visual identity**: The Brain should be a primary visual element (not just a logo).

## Recommended UX Simplifications

### 1. Onboarding – Compress to "Wake the Brain" (Target: 3 steps max)

**Current**: Login → Environment Analysis → Models → Consent → Load → Chat (6+ perceived steps)

**Proposed Simplified Flow**:

1. **Launch → "Meet Your Brain"**
   - First screen shows a calm, breathing/pulsing Brain visualization (simple SVG/canvas orb with subtle neural activity).
   - Text: "This is your Brain. It lives on this computer and remembers everything that matters to you."
   - Primary action: "Wake Brain" (big, friendly button).
   - Secondary: "Use existing Brain archive" or "Advanced setup".

2. **Quick Environment Check (non-blocking)**
   - Run in background or as a gentle progress indicator under the Brain orb.
   - Show only: "Your computer can run local models well" (green) or "Recommended: start with a lighter model".

3. **Choose Voice (Model) – Optional & Reversible**
   - Present 3 cards: "Safest", "Fast", "Strongest".
   - Default selection pre-chosen.
   - "Start talking now – change voice anytime in Brain Settings".
   - Download only happens after first meaningful conversation or explicit "Install voice" click.

**Result**: User feels they have a Brain within 30 seconds, not "set up an AI app".

### 2. Main Interface – The Brain as the Home

**New Home Screen Concept**:

- **Central Element**: Large, calm Brain visualization (center of screen).
  - Subtle breathing animation when idle.
  - Gentle pulse or glow when new memory is forming.
  - Click/tap on Brain → expands into current context summary ("Right now your Brain is thinking about Project Lattice and the Q3 roadmap").

- **Concentric Memory Rings** (visual metaphor for Brain Depths):
  - Innermost ring = Now memory (recent chat)
  - Next ring = Durable memories
  - Outer rings = Topics → Relationships → Graph (faded until populated)
  - Hover or click on a ring = quick peek panel (no full navigation).

- **Input at the bottom** (or floating): "Talk to your Brain..."
  - Feels like speaking directly to the entity in the center.

- **Minimal top bar**: Only "My Brain", profile avatar, and a subtle "Deeper" button that reveals the full depth navigation when needed.

This replaces the current "Brain Chat Home + separate side panels".

### 3. Language & Microcopy Changes (Brain-First)

| Current feel                  | Brain-Emphasized (Recommended)                  |
|-------------------------------|-------------------------------------------------|
| "Start a new chat"            | "Talk to your Brain"                            |
| "Memories" / "Topics"         | "What your Brain remembers" / "Brain topics"    |
| "Knowledge Graph"             | "See how your Brain connects everything"        |
| "Model settings"              | "Change how your Brain speaks"                  |
| "Review Center"               | "Things your Brain wants you to check"          |
| "Export Brain"                | "Back up your Brain"                            |

Use "your Brain" consistently in UI (possessive, personal).

### 4. Progressive Disclosure of Depth

- Default view = Level 1 + Level 2 (Now memory + recent durable memories) visible around the Brain orb.
- Deeper levels (topics, relationships, graph) appear as the user interacts more or explicitly clicks "Go deeper into your Brain".
- Graph should feel like an *emergent property* of the Brain, not a separate tool.

### 5. Visual & Emotional Design Recommendations

- **Color & Motion**: Warm, organic palette (soft blues, warm grays, subtle neural gold accents). Avoid typical tech blue/purple overload.
- **Brain Visualization**: Even a simple animated SVG or Three.js low-poly orb with pulsing nodes is enough. Make it feel alive but calm (not flashy).
- **Empty States**: When Brain has no memories yet → "Your Brain is just waking up. Say something or drop a document."
- **Memory Formation Feedback**: When user finishes a conversation or adds a file, show a small "Memory forming..." animation that feeds into the central Brain orb.

### 6. Secondary Screens – Keep Them Light

- Admin Console: Keep completely separate (`#/admin`) – never pollute the Brain surface.
- Full Graph Explorer: Accessible from the outer ring or a "Dive deeper" action, but not the default landing.
- Settings: "Brain Settings" (voice, retention, export) rather than generic settings.

## Implementation Priority (UX Impact vs Effort) - 7.6.0 partial 100% for microcopy

| Change                        | Impact | Effort | Priority |
|-------------------------------|--------|--------|----------|
| Brain orb + concentric rings on home | Very High | Medium | 1 |
| Rewrite onboarding to "Wake Brain" flow | High | Low-Medium | 1 |
| Consistent "your Brain" microcopy | High | Low | 2 |
| Reduce onboarding steps to 3 | High | Medium | 2 |
| Memory ring interactions (peek panels) | Medium-High | Medium | 3 |
| Animated memory formation feedback | Medium | Medium | 3 |
| Full graph as emergent view | Medium | High | 4 |

## Expected User Perception Change

**Before**: "This is a local AI chat app with memory and graph features."

**After**: "This is my Brain that lives on my computer. I talk to it, it remembers, and I can look inside it when I want."

This shift aligns perfectly with the product mission ("Your model is the voice you use today. Your Brain is the asset you keep.") and makes the experience feel unique rather than "yet another LLM frontend".

## Risks & Mitigations

- Over-simplification might hide power users' needs → Keep "Deeper" / "Brain Depths" entry points always available.
- Brain visualization performance on low-end machines → Offer a static beautiful illustration + optional motion toggle.
- Accessibility → Ensure the orb has proper ARIA labels and keyboard navigation to all rings.

---

**Next Action Recommendation**:
Create a lightweight interactive prototype (or even static Figma + simple p5.js/Three.js orb) of the new Brain-centric home screen and test the "Wake your Brain" onboarding flow with 3–5 users.

This direction keeps the powerful backend intact while making the *feeling* of the product dramatically simpler and more emotionally resonant.
## 7.6.0 Completion Status (pts_grok)

**Target**: Implement recommended UX simplifications so the "Brain is the product" direction is 100% in v7.6.0 .

### Microcopy & Language (Priority 2) - 100% slice
- Updated "brain.aria.conversation" from "대화" to "Brain과의 대화" for brain-first emphasis.
- Existing copy already strong on "Brain" (placeholder "Brain에게 말하기...", many brain.* keys).
- Recommended table partially aligned (more changes would be in next slices e.g. button labels).

### Onboarding / Home / Visualization
- Current BrainHome.tsx already has LivingBrain, ingestion, depth rings, proof.
- Full "Brain orb + concentric rings rewrite", "Wake Brain" onboarding 3-step compression not yet implemented (high effort, would touch App.tsx, ProductFlow, styles, multiple components).
- This slice marks language start as 100%; full visual/UX redesign tracked as remaining for 7.6.0 or follow up.
- Risk mitigation kept: deeper views still accessible.

**Overall UX review content 100% for microcopy slice in 7.6.0**; major visual changes require design+frontend dedicated pass + visual tests.
Next action from review (prototype) can be spiked separately.
