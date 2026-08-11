import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ChronicleDay } from "@/api/client";
import { renderPage } from "@/test/renderPage";
import "@/i18n/chronicle";
import { changeKindLabel, DayStory, entityTypeLabel, sourceTypeLabel } from "./DayStory";

/**
 * The day's story is where storage vocabulary would leak into a reader's face:
 * `web_url`, `Concept`, `fact_superseded` are all real values on this payload,
 * and basic mode is the default here. Each one is asserted as a sentence.
 *
 * The other property worth pinning is the difference between `counts` and
 * `groups`: the server caps a group at 200 and this component shows eight, so
 * "seven documents" with four rows on screen must still say seven.
 */

function day(overrides: Partial<ChronicleDay> = {}): ChronicleDay {
  return {
    date: "2026-06-03",
    counts: { sources: 2, entities: 1, conversations: 2, changes: 1 },
    groups: {
      sources: [
        { id: "prov-1", title: "retrieval-design.pdf", source_type: "upload", captured_at: "2026-06-03T09:20:00", node_id: "file:retrieval" },
        { id: "prov-2", title: "Lattice 소개 페이지", source_type: "web_url", captured_at: "2026-06-03T11:41:00", node_id: "web:intro" },
      ],
      entities: [
        { id: "entity:memory", label: "개인 기억", type: "Concept", created_at: "2026-06-03T09:33:00" },
      ],
      conversations: [
        { conversation_id: "conv-1", preview: "이번 릴리스 정리해 줘", messages: 12, started_at: "2026-06-03T09:15:00" },
        { conversation_id: "", preview: "", messages: 3, started_at: "2026-06-03T17:55:00" },
      ],
      changes: [
        { kind: "fact_superseded", label: "릴리스 절차", at: "2026-06-03T11:02:00", node_id: "entity:release" },
      ],
    },
    ...overrides,
  };
}

function render(value: ChronicleDay | null = day(), loading = false) {
  return renderPage(<DayStory day={value} loading={loading} language="ko" />);
}

describe("DayStory", () => {
  it("groups the day into the four things a person recognises", () => {
    render();
    for (const title of ["자료", "새로 생긴 개념", "나눈 대화", "달라진 사실"]) {
      expect(screen.getByRole("heading", { name: new RegExp(title) })).toBeTruthy();
    }
    expect(screen.getByTestId("chronicle-day-date")).toHaveTextContent("2026-06-03");
  });

  it("never shows a storage token to the reader", () => {
    render();
    const text = screen.getByTestId("chronicle-day").textContent || "";
    for (const token of ["web_url", "upload", "Concept", "fact_superseded"]) {
      expect(text, `${token} reached the screen`).not.toContain(token);
    }
    expect(screen.getByText("웹 페이지")).toBeTruthy();
    expect(screen.getByText("올린 파일")).toBeTruthy();
    expect(screen.getByText("주제")).toBeTruthy();
    expect(screen.getByText("새 내용으로 바뀐 사실")).toBeTruthy();
  });

  it("gives the loose-message card a name rather than an empty row", () => {
    render();
    expect(screen.getByText("제목 없는 대화")).toBeTruthy();
    expect(screen.getByText("3번 주고받음")).toBeTruthy();
  });

  it("deep-links each card into the surface that already shows that thing", async () => {
    render();
    window.location.hash = "";

    await userEvent.click(screen.getByRole("button", { name: /retrieval-design.pdf/ }));
    expect(window.location.hash).toBe("#/hybrid-search");

    await userEvent.click(screen.getByRole("button", { name: /개인 기억/ }));
    expect(window.location.hash).toBe("#/knowledge-graph");

    await userEvent.click(screen.getByRole("button", { name: /이번 릴리스 정리해 줘/ }));
    expect(window.location.hash).toBe("#/brain");
  });

  it("says how many it is not showing rather than dropping them", () => {
    render(day({ counts: { sources: 9, entities: 1, conversations: 2, changes: 1 } }));
    // Nine that day, two in the payload: the other seven are stated.
    expect(screen.getByText("이 밖에 7개가 더 있어요.")).toBeTruthy();
  });

  it("says nothing happened in a group rather than leaving a blank card", () => {
    const empty = day();
    empty.groups.changes = [];
    empty.counts.changes = 0;
    render(empty);
    expect(screen.getByText("이 날은 없어요.")).toBeTruthy();
  });

  it("calls a quiet day quiet instead of showing four empty cards", () => {
    render(day({
      counts: { sources: 0, entities: 0, conversations: 0, changes: 0 },
      groups: { sources: [], entities: [], conversations: [], changes: [] },
    }));
    expect(screen.getByTestId("chronicle-day-quiet")).toBeTruthy();
    expect(screen.queryAllByTestId("chronicle-group")).toHaveLength(0);
  });

  it("says it is loading rather than rendering an empty day", () => {
    render(day(), true);
    expect(screen.getByRole("status")).toHaveTextContent("그날 이야기를 불러오는 중입니다.");
    render(null);
    expect(screen.getAllByRole("status")[0]).toBeTruthy();
  });

  it("caps a very long group at eight rows", () => {
    const many = day();
    many.groups.entities = Array.from({ length: 20 }, (_, index) => ({
      id: `entity-${index}`, label: `개념 ${index}`, type: "Concept", created_at: "2026-06-03T09:00:00",
    }));
    many.counts.entities = 20;
    render(many);
    expect(screen.getByText("개념 7")).toBeTruthy();
    expect(screen.queryByText("개념 8")).toBeNull();
    expect(screen.getByText("이 밖에 12개가 더 있어요.")).toBeTruthy();
  });
});

describe("label tables", () => {
  it("falls back to a plain word for a token nobody translated", () => {
    expect(sourceTypeLabel("carrier_pigeon", "ko")).toBe("자료");
    expect(entityTypeLabel("Sasquatch", "ko")).toBe("개념");
    expect(changeKindLabel("something_new", "ko")).toBe("달라진 것");
  });

  it("uses the shell's own node-type table rather than keeping a second copy", () => {
    expect(entityTypeLabel("Document", "ko")).toBe("문서");
    expect(entityTypeLabel("Person", "en")).toBe("Person");
  });
});
