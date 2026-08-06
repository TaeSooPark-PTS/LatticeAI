import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { BrainCarePanel } from "./BrainCarePanel";

const STRONG = "Str0ng-Pass-123!";

function renderCare(api: Record<string, unknown> = {}) {
  return renderPage(<BrainCarePanel language="ko" />, { api });
}

async function expand() {
  await userEvent.click(screen.getByRole("button", { name: /내 Brain 돌보기/ }));
}

const pathInput = () => screen.getByLabelText("Brain 보관 파일 경로");
const passInput = () => screen.getByLabelText("Brain 보관 파일 비밀번호");
const confirmInput = () => screen.getByLabelText("Brain 보관 파일 비밀번호 확인");
const inspectButton = () => screen.getByRole("button", { name: /확인/ });
const restoreButton = () => screen.getByRole("button", { name: /복원 미리보기/ });

/** The three big care actions render label + detail; the detail is the stable handle. */
function careButton(detail: string) {
  const match = Array.from(document.querySelectorAll<HTMLButtonElement>("button.brain-care-button")).find(
    (button) => button.querySelector("small")?.textContent === detail,
  );
  if (!match) throw new Error(`care button with detail "${detail}" not found`);
  return match;
}
const exportButton = () => careButton("다른 곳으로 가져가기");
const backupButton = () => careButton("복사본 저장");
const archiveButton = () => careButton("암호화된 Brain");
const buttonLabel = (button: HTMLButtonElement) => button.querySelector("strong")?.textContent;

describe("BrainCarePanel shell", () => {
  it("stays collapsed until opened and proves ownership in the summary strip", async () => {
    const { container } = renderCare({
      graphPortability: ok({ archive_format: "brain-archive-v2" }),
      backupHealth: ok({ count: 3 }),
    });

    expect(container.querySelector("#brain-care-details")).toBeNull();
    expect(screen.getByText("개인 보관")).toBeTruthy();
    expect(await screen.findByText("brain-archive-v2")).toBeTruthy();
    expect(await screen.findByText("백업 3개")).toBeTruthy();

    const summary = screen.getByRole("button", { name: /내 Brain 돌보기/ });
    expect(summary.getAttribute("aria-expanded")).toBe("false");
    await expand();
    expect(summary.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector("#brain-care-details")).toBeTruthy();
    expect(
      screen.getByText("복원 미리보기는 Brain을 바꾸지 않고 보관 파일만 확인합니다. 실제 복원은 설정에서 확정합니다."),
    ).toBeTruthy();

    await expand();
    expect(container.querySelector("#brain-care-details")).toBeNull();
  });

  const backupLabels: Array<[Record<string, unknown>, string]> = [
    [{ backups: 2 }, "백업 2개"],
    [{ available: 5 }, "백업 5개"],
    [{}, "백업 준비됨"],
    [{ count: null, backups: null, available: null }, "백업 준비됨"],
    [{ available: "" }, "백업 준비됨"],
  ];
  it.each(backupLabels)("labels backup health %j as %s", async (data, expected) => {
    renderCare({ backupHealth: ok(data), graphPortability: ok(null) });
    expect(await screen.findByText(expected)).toBeTruthy();
    // Non-record portability payload falls back to the format name.
    expect(screen.getByText(".latticebrain")).toBeTruthy();
  });
});

describe("BrainCarePanel export/backup flows", () => {
  it("runs an export and summarizes path and contents from the envelope", async () => {
    renderCare({
      graphExport: ok({
        path: "/tmp/brain.json",
        message: "내보내기 완료",
        nodes: 10,
        edges: 4,
        conversations: 2,
      }),
    });
    await expand();
    await userEvent.click(exportButton());

    const result = await screen.findByText("내보내기 완료");
    expect(result.closest(".brain-care-result")?.className).toContain("is-ok");
    expect(screen.getByText("파일: /tmp/brain.json")).toBeTruthy();
    expect(screen.getByText("기억 10개 · 연결 4개 · 대화 2개")).toBeTruthy();
  });

  it("shows the in-flight label and disables the button until the export settles", async () => {
    let release!: (value: unknown) => void;
    renderCare({
      graphExport: () => new Promise<unknown>((resolve) => { release = resolve; }),
    });
    await expand();
    await userEvent.click(exportButton());

    await waitFor(() => expect(buttonLabel(exportButton())).toBe("진행 중"));
    expect(exportButton()).toBeDisabled();

    release(ok({ status: "끝" }));
    expect(await screen.findByText("끝")).toBeTruthy();
    expect(exportButton()).toBeEnabled();
  });

  it("demotes the backend detail under the friendly error, and hides it when absent or redundant", async () => {
    const exportMock = vi.fn()
      .mockResolvedValueOnce(fail("디스크가 가득 찼습니다", {}))
      .mockResolvedValueOnce(fail("Brain 관리 작업을 완료하지 못했습니다.", {}))
      .mockResolvedValueOnce({ ok: false, status: 503, source: "unavailable", data: {} });
    const { container } = renderCare({ graphExport: exportMock });
    await expand();
    const button = exportButton();

    await userEvent.click(button);
    await screen.findByText("디스크가 가득 찼습니다");
    expect(container.querySelector(".brain-care-result")?.className).toContain("is-error");
    expect(screen.getByText("Brain 관리 작업을 완료하지 못했습니다.")).toBeTruthy();

    // Detail identical to the friendly line is not repeated.
    await userEvent.click(button);
    await waitFor(() => expect(screen.queryByText("디스크가 가득 찼습니다")).toBeNull());
    expect(container.querySelectorAll(".brain-care-result-summary span")).toHaveLength(0);

    // No detail at all keeps only the friendly line.
    await userEvent.click(button);
    await waitFor(() => expect(exportMock).toHaveBeenCalledTimes(3));
    expect(container.querySelectorAll(".brain-care-result-summary span")).toHaveLength(0);
  });

  it("summarizes odd success payloads without inventing paths or counts", async () => {
    const exportMock = vi.fn()
      .mockResolvedValueOnce(ok(null))
      .mockResolvedValueOnce(ok({ edges: 3 }))
      .mockResolvedValueOnce(ok({ conversation_count: 7 }));
    const { container } = renderCare({ graphExport: exportMock });
    await expand();
    const button = exportButton();

    // Non-record data → generic completion, no path, no contents line.
    await userEvent.click(button);
    await screen.findByText("Brain 관리 작업 완료");
    expect(container.querySelectorAll(".brain-care-result-summary span")).toHaveLength(0);

    // Only edges known → the other counts print as 0, never NaN.
    await userEvent.click(button);
    expect(await screen.findByText("기억 0개 · 연결 3개 · 대화 0개")).toBeTruthy();

    // Only conversations known.
    await userEvent.click(button);
    expect(await screen.findByText("기억 0개 · 연결 0개 · 대화 7개")).toBeTruthy();
    expect(screen.queryByText(/파일:/)).toBeNull();
  });

  it("refreshes backup health and portability after a backup lands", async () => {
    renderCare({
      graphBackup: ok({ backup_path: "/backups/brain-1.zip", status: "백업 완료" }),
      backupHealth: ok({ count: 1 }),
    });
    await expand();
    const healthCalls = vi.mocked(latticeApi.backupHealth).mock.calls.length;
    const portabilityCalls = vi.mocked(latticeApi.graphPortability).mock.calls.length;

    await userEvent.click(backupButton());
    expect(await screen.findByText("백업 완료")).toBeTruthy();
    expect(screen.getByText("파일: /backups/brain-1.zip")).toBeTruthy();
    await waitFor(() => {
      expect(vi.mocked(latticeApi.backupHealth).mock.calls.length).toBeGreaterThan(healthCalls);
      expect(vi.mocked(latticeApi.graphPortability).mock.calls.length).toBeGreaterThan(portabilityCalls);
    });
  });
});

describe("BrainCarePanel archive gating", () => {
  it("walks the passphrase from too short to too simple to strong-and-matching", async () => {
    renderCare();
    await expand();
    expect(archiveButton()).toBeDisabled();

    fireEvent.change(passInput(), { target: { value: "short" } });
    expect(screen.getByText("비밀번호는 12자 이상이어야 합니다.")).toBeTruthy();
    expect(screen.getByText("비밀번호 확인이 일치하지 않습니다.")).toBeTruthy();
    expect(archiveButton()).toBeDisabled();

    fireEvent.change(passInput(), { target: { value: "aaaaaaaaaaaaaaaa" } });
    expect(screen.getByText("대문자, 소문자, 숫자, 기호 중 3종류 이상을 섞어 주세요.")).toBeTruthy();

    fireEvent.change(passInput(), { target: { value: STRONG } });
    expect(screen.getByText("비밀번호 강도가 충분합니다.")).toBeTruthy();
    expect(archiveButton()).toBeDisabled(); // confirm still empty → mismatch

    fireEvent.change(confirmInput(), { target: { value: STRONG } });
    expect(screen.queryByText("비밀번호 확인이 일치하지 않습니다.")).toBeNull();
    expect(archiveButton()).toBeEnabled();

    await userEvent.click(archiveButton());
    await waitFor(() =>
      expect(vi.mocked(latticeApi.brainArchive)).toHaveBeenCalledWith({ path: null, passphrase: STRONG }),
    );
  });

  it("shows the archive in-flight label while encrypting", async () => {
    let release!: (value: unknown) => void;
    renderCare({
      brainArchive: () => new Promise<unknown>((resolve) => { release = resolve; }),
    });
    await expand();
    fireEvent.change(passInput(), { target: { value: STRONG } });
    fireEvent.change(confirmInput(), { target: { value: STRONG } });
    await userEvent.click(archiveButton());

    await waitFor(() => expect(buttonLabel(archiveButton())).toBe("진행 중"));
    expect(archiveButton()).toBeDisabled();
    release(ok({ path: "/tmp/brain.latticebrain" }));
    expect(await screen.findByText("파일: /tmp/brain.latticebrain")).toBeTruthy();
  });

  it("validates the archive path and gates inspect/restore on it", async () => {
    renderCare({
      brainArchiveRestore: ok({ dry_run: true, message: "복원 예상 결과", nodes: 5 }),
    });
    await expand();
    expect(inspectButton()).toBeDisabled();
    expect(restoreButton()).toBeDisabled();

    fireEvent.change(pathInput(), { target: { value: "bad<name>.zip" } });
    expect(screen.getByText("보관 파일 경로에 사용할 수 없는 문자가 있습니다.")).toBeTruthy();
    expect(inspectButton()).toBeDisabled();

    fireEvent.change(pathInput(), { target: { value: "notes.txt" } });
    expect(screen.getByText(".latticebrain, .zip, .json 파일만 확인할 수 있습니다.")).toBeTruthy();

    fireEvent.change(pathInput(), { target: { value: "/tmp/brain.latticebrain" } });
    expect(screen.getByText("보관 파일 경로 형식이 올바릅니다.")).toBeTruthy();

    // Inspect works without a passphrase (unencrypted archives) and sends null.
    expect(inspectButton()).toBeEnabled();
    await userEvent.click(inspectButton());
    await waitFor(() =>
      expect(vi.mocked(latticeApi.brainArchiveInspect)).toHaveBeenCalledWith({
        path: "/tmp/brain.latticebrain",
        passphrase: null,
      }),
    );

    // Restore preview still needs a matching passphrase.
    expect(restoreButton()).toBeDisabled();
    fireEvent.change(passInput(), { target: { value: STRONG } });
    expect(restoreButton()).toBeDisabled(); // mismatch until confirmed
    fireEvent.change(confirmInput(), { target: { value: STRONG } });
    expect(restoreButton()).toBeEnabled();

    // With a passphrase present, inspect forwards it instead of null.
    await userEvent.click(inspectButton());
    await waitFor(() =>
      expect(vi.mocked(latticeApi.brainArchiveInspect)).toHaveBeenLastCalledWith({
        path: "/tmp/brain.latticebrain",
        passphrase: STRONG,
      }),
    );

    await userEvent.click(restoreButton());
    await waitFor(() =>
      expect(vi.mocked(latticeApi.brainArchiveRestore)).toHaveBeenCalledWith({
        path: "/tmp/brain.latticebrain",
        passphrase: STRONG,
        dry_run: true,
        confirm: false,
      }),
    );
    expect(await screen.findByText("복원 예상 결과")).toBeTruthy();
    expect(screen.getByText("미리보기만 실행되어 현재 Brain은 바뀌지 않았습니다.")).toBeTruthy();

    // A filled path also travels with a fresh archive request.
    await userEvent.click(archiveButton());
    await waitFor(() =>
      expect(vi.mocked(latticeApi.brainArchive)).toHaveBeenCalledWith({
        path: "/tmp/brain.latticebrain",
        passphrase: STRONG,
      }),
    );
  });

  it("locks inspect and restore while their previews are in flight", async () => {
    let releaseInspect!: (value: unknown) => void;
    let releaseRestore!: (value: unknown) => void;
    renderCare({
      brainArchiveInspect: () => new Promise<unknown>((resolve) => { releaseInspect = resolve; }),
      brainArchiveRestore: () => new Promise<unknown>((resolve) => { releaseRestore = resolve; }),
    });
    await expand();
    fireEvent.change(pathInput(), { target: { value: "/tmp/brain.zip" } });
    fireEvent.change(passInput(), { target: { value: STRONG } });
    fireEvent.change(confirmInput(), { target: { value: STRONG } });

    await userEvent.click(inspectButton());
    await waitFor(() => expect(inspectButton()).toBeDisabled());
    releaseInspect(ok({ status: "확인 완료" }));
    await screen.findByText("확인 완료");
    expect(inspectButton()).toBeEnabled();

    await userEvent.click(restoreButton());
    await waitFor(() => expect(restoreButton()).toBeDisabled());
    releaseRestore(ok({ status: "미리보기 완료" }));
    await screen.findByText("미리보기 완료");
    expect(restoreButton()).toBeEnabled();
  });
});
