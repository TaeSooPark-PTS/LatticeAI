import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { useRejectedSources } from "@/features/evidence/SourceFeedback";
import { fail, ok } from "@/test/renderPage";
import { jsonResponse, recordFetch } from "@/test/apiClientHarness";
import { useAppStore } from "@/store/appStore";
import { SearchFilters } from "./SearchFilters";
import { SearchResults } from "./SearchResults";
import { dayAfter, EMPTY_SEARCH_FILTERS, isReversedRange, searchFilterBody } from "./searchFilterModel";

beforeEach(() => {
  vi.restoreAllMocks();
  useRejectedSources.setState({ rejected: {} });
  useAppStore.setState({ language: "ko", workspaceId: "" } as never);
});

describe("search filter model", () => {
  it("sends nothing until a filter is set and makes the end date inclusive", () => {
    expect(searchFilterBody(EMPTY_SEARCH_FILTERS)).toBeUndefined();
    expect(searchFilterBody({ since: "2026-01-01", until: "2026-01-31", includeSuperseded: true }))
      .toEqual({ since: "2026-01-01", until: "2026-02-01", include_historical: true });
    expect(dayAfter("2026-12-31")).toBe("2027-01-01");
    expect(isReversedRange({ since: "2026-02-01", until: "2026-01-01", includeSuperseded: false })).toBe(true);
    expect(isReversedRange({ since: "2026-02-01", until: "", includeSuperseded: false })).toBe(false);
  });

  it("travels in the search request body", async () => {
    const calls = recordFetch(() => jsonResponse({ results: [] }));
    await latticeApi.hybridSearch("a", undefined, "relevance", { since: "2026-01-01" });
    await latticeApi.hybridSearchGraphRag("a", 12, undefined, { include_historical: true });
    expect(calls[0]!.body).toEqual({ query: "a", sort_by: "relevance", since: "2026-01-01" });
    expect(calls[1]!.body).toEqual({ query: "a", mode: "graphrag", limit: 12, include_historical: true });
  });
});

function Harness() {
  const [value, setValue] = React.useState(EMPTY_SEARCH_FILTERS);
  return <SearchFilters language="ko" value={value} onChange={setValue} />;
}

describe("SearchFilters", () => {
  it("edits, warns about a reversed range and clears", async () => {
    render(<Harness />);
    expect(screen.queryByRole("button", { name: "조건 지우기" })).toBeNull();
    await userEvent.type(screen.getByLabelText("수정일 시작"), "2026-03-01");
    await userEvent.type(screen.getByLabelText("수정일 끝"), "2026-02-01");
    expect(screen.getByRole("alert").textContent).toBe("시작일이 끝일보다 늦어요.");
    expect(screen.getByText("끝일이 지난 날짜이면 대체된 내용도 함께 나옵니다.")).toBeTruthy();
    await userEvent.click(screen.getByRole("checkbox", { name: "대체된 내용 포함" }));
    expect(screen.queryByText("끝일이 지난 날짜이면 대체된 내용도 함께 나옵니다.")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "조건 지우기" }));
    expect((screen.getByLabelText("수정일 시작") as HTMLInputElement).value).toBe("");
  });
});

describe("SearchResults", () => {
  it("explains a widened date window, the applied order and hidden items", () => {
    render(
      <SearchResults
        language="ko"
        query="q"
        data={{
          sort_by: "newest",
          temporal: { prefilter: { fallback: "no_documents_in_window" }, superseded: { excluded: 2 } },
          matches: [
            { node_id: "n1", title: "예산", summary: "요약", type: "Document" },
            { id: "n2", name: "이름만", snippet: "이름만" },
            { path: "/a.md" },
          ],
        }}
      />,
    );
    expect(screen.getByText("이 기간에 맞는 결과가 없어 전체 기간의 결과를 보여 드립니다.")).toBeTruthy();
    expect(screen.getByText("최신순 · 대체된 내용 2개 숨김")).toBeTruthy();
    expect(screen.getByText("#3")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /좋은 출처가 아니라고 표시/ })).toHaveLength(2);
  });

  it("reports hidden items even when no order is named", () => {
    render(<SearchResults language="ko" query="q" data={{ matches: [], temporal: { superseded: { excluded: 1 } } }} />);
    expect(screen.getByText("대체된 내용 1개 숨김")).toBeTruthy();
  });

  it("reads a bare list and says when nothing was found", () => {
    const view = render(<SearchResults language="ko" query="q" data={[{ id: "n1", title: "하나" }] as never} />);
    expect(screen.getByText("하나")).toBeTruthy();
    view.unmount();
    render(<SearchResults language="ko" query="q" data={{ matches: [], sort_by: "relevance" }} />);
    expect(screen.getByText("관련도순")).toBeTruthy();
    expect(screen.getByText("찾은 결과가 없어요")).toBeTruthy();
  });
});

describe("source feedback", () => {
  const data = { matches: [{ id: "n1", title: "예산" }] };
  const reject = () => screen.getByRole("button", { name: "예산: 좋은 출처가 아니라고 표시" });

  it("records a citation correction and takes it back", async () => {
    const post = vi.spyOn(latticeApi, "rejectCitation").mockResolvedValue(ok({ correction_id: "corr:1" }) as never);
    const revert = vi.spyOn(latticeApi, "revertCorrection").mockResolvedValue(ok({}) as never);
    render(<SearchResults language="ko" query="질문" data={data} />);
    await userEvent.click(reject());
    expect(post).toHaveBeenCalledWith("n1", "질문");
    expect(screen.getByText("좋지 않은 출처로 기록했어요.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "예산: 출처 표시 되돌리기" }));
    expect(revert).toHaveBeenCalledWith("corr:1");
    expect(reject()).toBeTruthy();
  });

  it("does not claim success when the server refuses or returns no id", async () => {
    useAppStore.setState({ workspaceId: "org" } as never);
    vi.spyOn(latticeApi, "rejectCitation")
      .mockResolvedValueOnce(fail("노드가 없어요", {}) as never)
      .mockResolvedValueOnce(ok({}) as never);
    render(<SearchResults language="ko" query="q" data={data} />);
    await userEvent.click(reject());
    expect(screen.getByRole("alert").textContent).toBe("노드가 없어요");
    await userEvent.click(reject());
    expect(screen.getByRole("alert").textContent).toBe("출처 표시를 저장하지 못했어요.");
  });
});
