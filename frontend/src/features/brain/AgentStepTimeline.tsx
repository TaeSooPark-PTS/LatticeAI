import { t, type Language } from "@/i18n";
import type { AgentStepEvent, MessageLoopSummary } from "./types";

// How each loop event renders: a styled dot (CSS class, never an emoji in
// code) + a short i18n label. Unknown events fall back to a neutral marker so
// future backend additions degrade gracefully.
type StepKind = "ok" | "error" | "blocked" | "proposed" | "info";

export function stepKind(step: AgentStepEvent): StepKind {
  if (step.event === "blocked") return "blocked";
  if (step.event === "parse_error") return "error";
  if (step.ok === false) return "error";
  if (step.event === "proposed") return "proposed";
  if (step.event === "tool") return "ok";
  if (step.phase === "rollback") return "error";
  return "info";
}

function stepLabelKey(step: AgentStepEvent): string {
  if (step.phase === "rollback") return "brain.steps.rollback";
  switch (step.event) {
    case "planned":
      return "brain.steps.planned";
    case "tool":
      return step.ok === false ? "brain.steps.tool.failed" : "brain.steps.tool";
    case "proposed":
      return "brain.steps.proposed";
    case "blocked":
      return "brain.steps.blocked";
    case "parse_error":
      return "brain.steps.parseError";
    case "verdict":
      return "brain.steps.verdict";
    case "final":
      return "brain.steps.final";
    case "state":
      return "brain.steps.state";
    default:
      return "brain.steps.step";
  }
}

function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

// Machine detail shown next to the label: tool name, target file, decision/
// verdict/state values. Raw data values, never display copy.
function stepMeta(step: AgentStepEvent): string {
  return [
    step.action,
    step.path ? baseName(step.path) : "",
    step.decision,
    step.verdict,
    step.state,
  ]
    .filter(Boolean)
    .join(" · ");
}

const MAX_VISIBLE_STEPS = 30;

// Compact vertical timeline of what the agent loop actually did — live while
// the run streams (`event: agent_step` frames), collapsed to an expandable
// one-line summary once the reply is done.
export function AgentStepTimeline({
  language,
  steps,
  streaming,
}: {
  language: Language;
  steps: AgentStepEvent[];
  streaming: boolean;
}) {
  if (!steps.length) return null;
  const visible = steps.slice(-MAX_VISIBLE_STEPS);
  const list = (
    <ol className="brain-step-list">
      {visible.map((step, index) => {
        const kind = stepKind(step);
        const meta = stepMeta(step);
        return (
          <li key={index} className={`brain-step-item is-${kind}`}>
            <span className="brain-step-dot" aria-hidden="true" />
            <span className="brain-step-label">{t(language, stepLabelKey(step))}</span>
            {meta ? <span className="brain-step-meta">{meta}</span> : null}
            {step.detail ? <small className="brain-step-detail">{step.detail}</small> : null}
          </li>
        );
      })}
    </ol>
  );

  if (streaming) {
    return (
      <section
        className="brain-step-timeline is-live"
        aria-label={t(language, "brain.steps.aria")}
        data-testid="agent-step-timeline"
      >
        <p className="brain-step-live-head" role="status">
          <span className="brain-step-dot is-live" aria-hidden="true" />
          {t(language, "brain.steps.live", { count: steps.length })}
        </p>
        {list}
      </section>
    );
  }

  return (
    <details
      className="brain-step-timeline"
      aria-label={t(language, "brain.steps.aria")}
      data-testid="agent-step-timeline"
    >
      <summary>{t(language, "brain.steps.summary", { count: steps.length })}</summary>
      {list}
    </details>
  );
}

// Loop transparency note: "모델 응답을 N회 보정했어요" — shown only when the
// loop actually repaired model output (works for DONE runs too, where no
// warning strip renders). The tooltip lists the top repair kinds.
export function LoopRepairsNote({
  language,
  summary,
}: {
  language: Language;
  summary: MessageLoopSummary;
}) {
  if (summary.total < 1) return null;
  const kinds = Object.entries(summary.repairs)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3)
    .map(([kind, count]) => `${kind} ×${count}`);
  if (summary.parseRecovered > 0) kinds.push(`parse ×${summary.parseRecovered}`);
  return (
    <p
      className="brain-loop-repairs"
      role="note"
      data-testid="loop-repairs-note"
      title={kinds.join(", ") || undefined}
    >
      {t(language, "brain.agent.repairs", { count: summary.total })}
    </p>
  );
}
