# Lattice AI v6.0.0 UX Review

## Product Promise

Lattice AI should read as a local-first Digital Brain, not as another chat or
IDE clone. The product should answer five questions quickly:

1. What is Lattice AI?
2. Where is my data stored?
3. What model is running?
4. What leaves my machine?
5. What should I do next?

## Review Center UX

Implemented improvements:

- Review Center is separated under Act > Runs > Review.
- Status filters include Pending, Snoozed, and All.
- Source filters include All, Workflow, Trigger, and KG digest.
- Snoozed items show `snoozed_until`.
- Snoozed items expose Unsnooze, Dismiss, and Run now.
- Pending items expose Run now, Approve, Snooze, and Dismiss.
- Approved and dismissed items are read-only when shown.
- `run_now` feedback says Executed/Regenerated and does not imply approval.

## Remaining UX Gaps

- Brain Home still needs a clearer "what should I do next" path.
- Onboarding still needs stronger local-storage and "what leaves my machine"
  signposting.
- Model-running state is visible in places, but not yet presented as a single
  persistent trust indicator.
- Automation explanations are clearer in Review Center but still need a
  product-wide vocabulary pass.

## UX Risk

The Review Center is more reversible now, but adding filters and actions can
raise complexity. Keep the default Pending view quiet and keep Snoozed/All as
escape hatches for power users.
