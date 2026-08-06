import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { makeBrief, makeConversations, makeProof, makeReadiness } from "@/test/brainFixtures";
import type { BrainBriefAction, BrainProof, EmergenceEvent } from "./types";
import {
  BrainBriefPanel,
  formatConversationTime,
  handleBriefAction,
  ModelContinuityDemo,
  ModelMissingNotice,
  PastConversationsPanel,
  ProductCommandCenter,
} from "./HomePanels";

afterEach(() => {
  window.location.hash = "";
});

describe("ModelMissingNotice", () => {
  it("renders the pill and routes to /models on the CTA", () => {
    render(<ModelMissingNotice language="ko" />);
    expect(screen.getByRole("note")).toHaveAttribute("title", t("ko", "brain.noModel.banner"));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.noModel.cta")) }));
    expect(window.location.hash).toBe("#/models");
  });
});

describe("formatConversationTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 5, 15, 12, 0, 0));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns an empty string without a timestamp", () => {
    expect(formatConversationTime("ko", undefined)).toBe("");
    expect(formatConversationTime("ko", 0)).toBe("");
  });

  it("renders a clock time for today and a date otherwise", () => {
    const today = new Date(2026, 5, 15, 9, 30).getTime();
    const sameMonthOtherDay = new Date(2026, 5, 14, 9, 30).getTime();
    const otherMonth = new Date(2026, 2, 15, 9, 30).getTime();
    const otherYear = new Date(2025, 5, 15, 9, 30).getTime();

    expect(formatConversationTime("ko", today)).toMatch(/09|9/);
    // Every non-today bucket falls back to a short date, not a clock time.
    for (const stamp of [sameMonthOtherDay, otherMonth, otherYear]) {
      expect(formatConversationTime("ko", stamp)).not.toMatch(/:/);
      expect(formatConversationTime("ko", stamp)).toBeTruthy();
    }
  });

  it("formats with the ko and en locales", () => {
    const otherDay = new Date(2026, 4, 3, 9, 30).getTime();
    expect(formatConversationTime("ko", otherDay)).toMatch(/5/);
    expect(formatConversationTime("en", otherDay)).toMatch(/May/);
  });
});

describe("PastConversationsPanel", () => {
  const noop = () => {};

  it("renders nothing when there is no history", () => {
    const { container } = render(
      <PastConversationsPanel language="ko" items={[]} busyId={null} onResume={noop} onDelete={noop} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("collapses to five items and expands to twenty on 더 보기", () => {
    const { container } = render(
      <PastConversationsPanel language="ko" items={makeConversations(25)} busyId={null} onResume={noop} onDelete={noop} />,
    );
    expect(container.querySelectorAll(".brain-history-item")).toHaveLength(5);

    const more = screen.getByRole("button", { name: t("ko", "brain.history.showMore", { count: 15 }) });
    expect(more).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(more);
    expect(container.querySelectorAll(".brain-history-item")).toHaveLength(20);

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.history.showLess") }));
    expect(container.querySelectorAll(".brain-history-item")).toHaveLength(5);
  });

  it("hides the toggle when everything already fits", () => {
    const { container } = render(
      <PastConversationsPanel language="ko" items={makeConversations(3)} busyId={null} onResume={noop} onDelete={noop} />,
    );
    expect(container.querySelector(".brain-history-more")).toBeNull();
  });

  it("resumes a conversation and omits the time suffix when absent", () => {
    const onResume = vi.fn();
    const items = [{ id: "c1", title: "무제 대화", messageCount: 4 }];
    render(
      <PastConversationsPanel language="ko" items={items} busyId={null} onResume={onResume} onDelete={noop} />,
    );
    const resume = screen.getByRole("button", { name: t("ko", "brain.history.resumeAria", { title: "무제 대화" }) });
    expect(resume.textContent).not.toContain("·");
    fireEvent.click(resume);
    expect(onResume).toHaveBeenCalledWith("c1");
  });

  it("deletes only after the two-step confirm", () => {
    const onDelete = vi.fn();
    render(
      <PastConversationsPanel language="ko" items={makeConversations(2)} busyId={null} onResume={noop} onDelete={onDelete} />,
    );
    const del = screen.getByRole("button", { name: t("ko", "brain.history.deleteAria", { title: "대화 1" }) });
    fireEvent.click(del);
    expect(onDelete).not.toHaveBeenCalled();
    expect(del).toHaveAccessibleName(t("ko", "brain.history.deleteConfirm"));
    expect(del.querySelector("span")).toBeTruthy();

    fireEvent.click(del);
    expect(onDelete).toHaveBeenCalledWith("conv-1");
    expect(del.querySelector("span")).toBeNull();
  });

  it("cancels the confirm when the delete button loses focus", () => {
    const onDelete = vi.fn();
    render(
      <PastConversationsPanel language="ko" items={makeConversations(2)} busyId={null} onResume={noop} onDelete={onDelete} />,
    );
    const first = screen.getByRole("button", { name: t("ko", "brain.history.deleteAria", { title: "대화 1" }) });
    const second = screen.getByRole("button", { name: t("ko", "brain.history.deleteAria", { title: "대화 2" }) });

    // Blur of an unrelated delete button must not clear another row's confirm.
    fireEvent.click(first);
    fireEvent.blur(second);
    expect(first).toHaveAccessibleName(t("ko", "brain.history.deleteConfirm"));

    fireEvent.blur(first);
    expect(first).toHaveAccessibleName(t("ko", "brain.history.deleteAria", { title: "대화 1" }));
    fireEvent.click(first);
    fireEvent.click(first);
    expect(onDelete).toHaveBeenCalledWith("conv-1");
  });

  it("disables the busy row and shows the spinner", () => {
    const { container } = render(
      <PastConversationsPanel language="ko" items={makeConversations(2)} busyId="conv-1" onResume={noop} onDelete={noop} />,
    );
    const busyRow = container.querySelector(".brain-history-item.is-busy") as HTMLElement;
    expect(busyRow).toBeTruthy();
    expect(busyRow.querySelector(".brain-ingest-spin")).toBeTruthy();
    for (const button of busyRow.querySelectorAll("button")) {
      expect(button).toBeDisabled();
    }
  });
});

describe("handleBriefAction", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("focuses the composer for ask_brain", () => {
    const wrapper = document.createElement("div");
    wrapper.className = "brain-composer";
    const textarea = document.createElement("textarea");
    wrapper.appendChild(textarea);
    document.body.appendChild(wrapper);

    const verify = vi.fn();
    handleBriefAction({ id: "ask_brain", labelKey: "", detailKey: "", route: "/ignored", priority: 1 }, verify);
    expect(document.activeElement).toBe(textarea);
    expect(verify).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("");
  });

  it("verifies model continuity for verify_model", () => {
    const verify = vi.fn();
    handleBriefAction({ id: "verify_model", labelKey: "", detailKey: "", route: "/ignored", priority: 1 }, verify);
    expect(verify).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toBe("");
  });

  it("navigates when the action carries a route and stays put otherwise", () => {
    const verify = vi.fn();
    handleBriefAction({ id: "add_source", labelKey: "", detailKey: "", route: "/capture", priority: 1 }, verify);
    expect(window.location.hash).toBe("#/capture");

    window.location.hash = "";
    handleBriefAction({ id: "unknown", labelKey: "", detailKey: "", route: "", priority: 1 }, verify);
    expect(window.location.hash).toBe("");
    expect(verify).not.toHaveBeenCalled();
  });
});

describe("BrainBriefPanel", () => {
  it("renders the focus, evidence and up to three actions", () => {
    const onAction = vi.fn();
    const brief = makeBrief();
    render(<BrainBriefPanel language="ko" brief={brief} onAction={onAction} />);

    expect(screen.getByText("프로젝트 계획", { selector: ".brain-brief-focus strong" })).toBeTruthy();
    expect(screen.getByLabelText(t("ko", "brain.brief.evidence.aria"))).toBeTruthy();

    const actions = screen.getAllByRole("button");
    expect(actions).toHaveLength(3);
    fireEvent.click(actions[0]);
    expect(onAction).toHaveBeenCalledWith(brief.nextActions[0]);
  });

  it("falls back to the empty focus copy and hides evidence when asked", () => {
    const brief = makeBrief({
      focus: { kind: "none", title: "", detail: "", source: "", score: 0, empty: true },
      nextActions: [],
    });
    const { container } = render(
      <BrainBriefPanel language="ko" brief={brief} showEvidence={false} onAction={() => {}} />,
    );
    expect(screen.getByText(t("ko", "brain.brief.focus.empty"))).toBeTruthy();
    expect(screen.getByText(t("ko", "brain.brief.focus.empty.detail"))).toBeTruthy();
    expect(container.querySelector(".brain-brief-evidence")).toBeNull();
    expect(container.querySelector(".brain-brief-actions")).toBeNull();
  });

  it("picks an icon per known action id and an arrow for the rest", () => {
    const withIcons = makeBrief({
      nextActions: [
        { id: "add_source", labelKey: "brain.brief.action.add", detailKey: "brain.brief.action.add.detail", route: "", priority: 1 },
        { id: "inspect_topics", labelKey: "brain.brief.action.topics", detailKey: "brain.brief.action.topics.detail", route: "", priority: 2 },
        { id: "verify_model", labelKey: "brain.brief.action.verify", detailKey: "brain.brief.action.verify.detail", route: "", priority: 3 },
      ],
    });
    const { container, rerender } = render(
      <BrainBriefPanel language="ko" brief={withIcons} onAction={() => {}} />,
    );
    expect(container.querySelectorAll(".brain-brief-actions button svg")).toHaveLength(3);

    rerender(
      <BrainBriefPanel
        language="ko"
        brief={makeBrief({
          nextActions: [
            { id: "backup_brain", labelKey: "brain.brief.action.backup", detailKey: "brain.brief.action.backup.detail", route: "", priority: 1 },
            { id: "ask_brain", labelKey: "brain.brief.action.ask", detailKey: "brain.brief.action.ask.detail", route: "", priority: 2 },
          ],
        })}
        onAction={() => {}}
      />,
    );
    expect(container.querySelectorAll(".brain-brief-actions button svg")).toHaveLength(2);
  });
});

describe("ProductCommandCenter", () => {
  const baseProps = {
    language: "ko" as const,
    readiness: makeReadiness(),
    proof: makeProof(),
    modelName: "fallback-model",
    memories: [],
    concepts: [],
    emergenceEvents: [] as EmergenceEvent[],
  };

  it("wires the four actions to their destinations", () => {
    const onOpenDepth = vi.fn();
    const onVerify = vi.fn();
    render(<ProductCommandCenter {...baseProps} onOpenDepth={onOpenDepth} onVerifyModelContinuity={onVerify} />);

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.command.action.add") }));
    expect(window.location.hash).toBe("#/capture");
    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.command.action.find") }));
    expect(onOpenDepth).toHaveBeenCalledWith(3);
    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.command.action.proof") }));
    expect(onVerify).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.command.action.own") }));
    expect(window.location.hash).toBe("#/settings");

    // Proven continuity shows the active model on the proof action.
    expect(screen.getByText("mlx-community/test-model")).toBeTruthy();
    expect(screen.getByText(t("ko", "brain.command.next.alive"))).toBeTruthy();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "62");
  });

  it("labels the next step per readiness state", () => {
    const noop = () => {};
    const { rerender } = render(
      <ProductCommandCenter {...baseProps} readiness={makeReadiness({ state: "forming" })} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getByText(t("ko", "brain.command.next.forming"))).toBeTruthy();

    rerender(
      <ProductCommandCenter {...baseProps} readiness={makeReadiness({ state: "quiet" })} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getByText(t("ko", "brain.command.next.empty"))).toBeTruthy();
  });

  it("counts recallable proof from recall items, then durable items, then one", () => {
    const noop = () => {};
    const { rerender } = render(
      <ProductCommandCenter {...baseProps} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getByText(t("ko", "brain.command.metric.proof", { count: 1 }))).toBeTruthy();

    const noRecall = { ...makeProof(), recall: undefined as unknown as BrainProof["recall"] };
    rerender(
      <ProductCommandCenter {...baseProps} proof={noRecall} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getByText(t("ko", "brain.command.metric.proof", { count: 5 }))).toBeTruthy();

    const zeroDurable = {
      ...makeProof({ proofs: { ...makeProof().proofs, durableItems: 0 } }),
      recall: undefined as unknown as BrainProof["recall"],
    };
    rerender(
      <ProductCommandCenter {...baseProps} proof={zeroDurable} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getByText(t("ko", "brain.command.metric.proof", { count: 1 }))).toBeTruthy();

    const noProofs = {
      ...makeProof(),
      recall: undefined as unknown as BrainProof["recall"],
      proofs: undefined as unknown as BrainProof["proofs"],
    };
    rerender(
      <ProductCommandCenter {...baseProps} proof={noProofs} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getByText(t("ko", "brain.command.metric.proof", { count: 1 }))).toBeTruthy();
  });

  it("shows the latest source label and model fallbacks", () => {
    const noop = () => {};
    const events: EmergenceEvent[] = [
      { id: "e1", sourceType: "file", label: "보고서.pdf", newMemories: 2, newEntities: 1, nodeIds: [], at: 1 },
    ];
    const { rerender } = render(
      <ProductCommandCenter {...baseProps} emergenceEvents={events} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getByText("보고서.pdf")).toBeTruthy();

    // Proven but with an empty active model name → the passed model name.
    const provenNoName = makeProof();
    provenNoName.modelContinuity = { ...provenNoName.modelContinuity, activeModel: "" };
    rerender(
      <ProductCommandCenter {...baseProps} proof={provenNoName} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getAllByText("fallback-model").length).toBeGreaterThan(0);

    const unproven = makeProof();
    unproven.modelContinuity = { ...unproven.modelContinuity, proven: false };
    rerender(
      <ProductCommandCenter {...baseProps} proof={unproven} onOpenDepth={noop} onVerifyModelContinuity={noop} />,
    );
    expect(screen.getAllByText("fallback-model").length).toBeGreaterThan(0);
  });
});

describe("ModelContinuityDemo", () => {
  it("shows the proven state and wires verify/change", () => {
    const onVerify = vi.fn();
    render(<ModelContinuityDemo language="ko" proof={makeProof()} modelName="fallback" onVerify={onVerify} />);
    expect(screen.getByText(t("ko", "brain.modelDemo.proven"))).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.modelDemo.verify")) }));
    expect(onVerify).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.modelDemo.change")) }));
    expect(window.location.hash).toBe("#/models");
  });

  it("shows the pending state and falls back to the passed model name", () => {
    const pending = makeProof();
    pending.modelContinuity = { ...pending.modelContinuity, proven: false, activeModel: "" };
    render(<ModelContinuityDemo language="ko" proof={pending} modelName="fallback" onVerify={() => {}} />);
    expect(screen.getByText(t("ko", "brain.modelDemo.pending"))).toBeTruthy();
    expect(screen.getByText(t("ko", "brain.modelDemo.detail", { model: "fallback" }))).toBeTruthy();
  });
});
