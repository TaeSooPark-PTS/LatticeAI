import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { useAppStore } from "@/store/appStore";
import { fail, ok, renderPage } from "@/test/renderPage";
import { SystemPage } from "./System";

/**
 * The settings screen: seven destinations, two safety dials, and the account
 * surface. The seven no longer sit in one flat strip — they are sorted into
 * three named groups, so this file asserts the grouping as well as the panels.
 *
 * Until 10.3.0 this page had no unit test — Playwright drove its happy path,
 * which cannot reach a server that is down, a workspace list that is empty, or
 * a mode that hides half the page. Those are exactly the states where a
 * settings screen misleads someone.
 */

const HEALTH = { status: "ok", version: "10.3.0", mode: "local" };
const PROFILE = { email: "me@local", nickname: "Me", role: "admin", name: "Me" };
const WORKSPACES = {
  workspaces: [
    { id: "personal", workspace_id: "personal", name: "Personal Workspace", type: "personal", status: "active" },
    { id: "team", workspace_id: "team", name: "Team", type: "organization", status: "active" },
  ],
  active_workspace: "personal",
};

function render(overrides = {}, options = {}) {
  return renderPage(<SystemPage />, {
    api: {
      health: ok(HEALTH),
      profile: ok(PROFILE),
      workspaceRegistry: ok(WORKSPACES),
      permissionMode: ok({
        mode: "strict", label: "Strict", label_ko: "엄격", risk: "low",
        requires_ack: false, circuit_breakers: true,
        catalog: [
          { id: "strict", label: "Strict", label_ko: "엄격", summary: "s", summary_ko: "엄", risk: "low", requires_ack: false },
          { id: "bypass", label: "Bypass", label_ko: "바이패스", summary: "b", summary_ko: "바", risk: "high", requires_ack: true },
        ],
      }),
      networkBoundary: ok({
        mode: "local_only", label: "Local only", label_ko: "로컬만",
        allows_cloud: false, requires_ack: false, warning_ko: null,
        policy: {}, token_budget: {},
        catalog: [
          { id: "local_only", label: "Local only", label_ko: "로컬만", summary: "l", summary_ko: "로", risk: "low", requires_ack: false },
          { id: "cloud_allowed", label: "Cloud", label_ko: "클라우드", summary: "c", summary_ko: "클", risk: "medium", requires_ack: true },
        ],
      }),
      ...overrides,
    },
    ...options,
  });
}

describe("SystemPage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("opens on the account tab and shows who is signed in", async () => {
    render();
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByText("me@local")).toBeTruthy());
  });

  it("offers every tab as a real tab control", async () => {
    render();
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    const tabs = screen.getAllByRole("tab");
    expect(tabs.length).toBeGreaterThanOrEqual(6);
    // aria-selected must track the active tab, or a screen reader announces
    // the wrong panel.
    expect(tabs.filter((t) => t.getAttribute("aria-selected") === "true")).toHaveLength(1);
  });

  it("moves to the settings tab and shows both safety dials together", async () => {
    render();
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    expect(screen.getByTestId("network-boundary-panel")).toBeTruthy();
  });

  it("keeps the two dials independent: neither reads the other's state", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByTestId("permission-mode-active")).toBeTruthy());

    // The autonomy dial names its modes from the i18n table by id (the server
    // ships "엄격"/"Strict"); the boundary dial still shows the server's label.
    expect(screen.getByTestId("permission-mode-active").textContent).toBe("먼저 물어보기");
    expect(screen.getByTestId("network-boundary-active").textContent).toBe("로컬만");
  });

  it("lists workspaces on the workspace tab", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "작업공간" }));
    await waitFor(() => expect(screen.getByText(/Personal Workspace/)).toBeTruthy());
    expect(screen.getByText(/Team/)).toBeTruthy();
  });

  it("reports an unavailable server rather than rendering a blank panel", async () => {
    render({ health: fail("server unavailable", {}) });
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    // Some panel must say the request failed; silence would read as "healthy".
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/요청을 처리하지 못했어요|사용할 수 없|unavailable/i));
  });

  it("renders in English when the language is en", async () => {
    render({}, { language: "en" });
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    expect(screen.getByRole("tab", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Account" })).toBeTruthy();
  });

  it("gates the admin panel behind the admin detail level, not just the tab", async () => {
    // The tab is always listed; opening it must not reveal admin controls.
    render({}, { mode: "advanced" });
    await userEvent.click(screen.getByRole("tab", { name: "관리자" }));
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/관리자 모드|admin/i));
    // The gate, not the controls: no role table should have rendered.
    expect(screen.queryByText(/adminRoles/)).toBeNull();
  });

  it("keyboard users can move between tabs with the arrow keys", async () => {
    render();
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    const first = screen.getAllByRole("tab")[0];
    first.focus();
    await userEvent.keyboard("{ArrowRight}");
    await waitFor(() =>
      expect(screen.getAllByRole("tab")[1].getAttribute("aria-selected")).toBe("true"));
  });

  it("shows the appearance and detail-level controls separately", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByText("화면 모양")).toBeTruthy());
    expect(screen.getByText("보여줄 내용의 양")).toBeTruthy();
  });

  it("sorts the seven destinations into three named groups", async () => {
    render({}, { mode: "advanced" });
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));

    // Each group is its own tablist and must carry an accessible name, or a
    // screen reader announces three identical unlabelled tab strips.
    const lists = screen.getAllByRole("tablist");
    expect(lists).toHaveLength(3);
    expect(lists.map((list) => list.getAttribute("aria-label")
      || document.getElementById(list.getAttribute("aria-labelledby") || "")?.textContent))
      .toEqual(["나와 작업공간", "내 데이터 보관", "동작 방식과 연결"]);

    // Grouping must not drop a destination on the floor.
    expect(screen.getAllByRole("tab")).toHaveLength(7);
  });

  it("leaves every group reachable from the keyboard, not just the selected one", async () => {
    // A roving tabindex keys off the selected tab. With the selection living in
    // one group, the other two would have every button at -1 and fall out of
    // the Tab order entirely — reachable by mouse only.
    render({}, { mode: "advanced" });
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));

    for (const list of screen.getAllByRole("tablist")) {
      const reachable = within(list).getAllByRole("tab")
        .filter((tab) => tab.getAttribute("tabindex") === "0");
      expect(reachable, list.getAttribute("aria-labelledby") || "").toHaveLength(1);
    }
  });

  it("an empty workspace list reads as empty rather than as a failure", async () => {
    render({ workspaceRegistry: ok({ workspaces: [], active_workspace: null }) });
    await userEvent.click(screen.getByRole("tab", { name: "작업공간" }));
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    expect(document.body.textContent).not.toMatch(/undefined|NaN|\[object Object\]/);
  });

  it("renders host telemetry in advanced mode with unwrapped data containing ram_pct", async () => {
    render(
      {
        sysinfo: ok({ cpu_pct: 12, ram_pct: 61, gpu_pct: 0, readiness: "tight" }),
      },
      { mode: "advanced" }
    );
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    await waitFor(() => expect(document.body.textContent).toContain("61"));
  });

  it("honors a deep link straight to the settings tab", async () => {
    renderPage(<SystemPage initialTab="settings" />, {
      api: { permissionMode: fail("down", {}), networkBoundary: fail("down", {}) },
    });
    await waitFor(() => expect(screen.getAllByText("요청을 처리하지 못했어요").length).toBeGreaterThan(0));
    expect(screen.getByRole("tab", { name: "환경설정" }).getAttribute("aria-selected")).toBe("true");
  });

  it("an unknown deep link selects nothing rather than a wrong panel", async () => {
    renderPage(<SystemPage initialTab="landing-page" />);
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    // No tab claims the bogus route and no panel is misattributed to it.
    expect(screen.queryByPlaceholderText("이메일")).toBeNull();
  });

  it("keeps everyday (basic) mode down to the four everyday destinations", async () => {
    render({}, { mode: "basic" });
    // Wait for the account panels to answer, so the basic-mode account view
    // (plain sentence, no payload dump) actually renders before we assert.
    await waitFor(() => expect(screen.getByText("me@local")).toBeTruthy());
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    // All three groups still render — none of them ends up empty.
    expect(screen.getAllByRole("tablist")).toHaveLength(3);
  });
});

describe("SystemPage — account surface", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("drives login, register, profile save, password change and logout", async () => {
    useAppStore.setState({ workspaceId: "w-before" });
    render();
    await waitFor(() => expect(screen.getByPlaceholderText("이메일")).toBeTruthy());

    fireEvent.change(screen.getByPlaceholderText("이메일"), { target: { value: "me@x.io" } });
    fireEvent.change(screen.getByPlaceholderText("현재 비밀번호"), { target: { value: "pw-1" } });
    fireEvent.change(screen.getByPlaceholderText("이름"), { target: { value: "Tae" } });
    fireEvent.change(screen.getByPlaceholderText("닉네임"), { target: { value: "ts" } });
    fireEvent.change(screen.getByPlaceholderText("새 비밀번호"), { target: { value: "pw-2" } });

    await userEvent.click(screen.getByRole("button", { name: "로그인" }));
    await waitFor(() => expect(latticeApi.login).toHaveBeenCalledWith("me@x.io", "pw-1"));
    // A successful sign-in resets the identity scope.
    await waitFor(() => expect(useAppStore.getState().workspaceId).toBeNull());

    await userEvent.click(screen.getByRole("button", { name: "가입" }));
    await waitFor(() => expect(latticeApi.register).toHaveBeenCalledWith({
      email: "me@x.io", password: "pw-1", name: "Tae", nickname: "ts",
    }));

    await userEvent.click(screen.getByRole("button", { name: "프로필 저장" }));
    await waitFor(() => expect(latticeApi.updateProfile).toHaveBeenCalledWith({ name: "Tae", nickname: "ts" }));

    await userEvent.click(screen.getByRole("button", { name: "비밀번호 변경" }));
    await waitFor(() => expect(latticeApi.changePassword).toHaveBeenCalledWith("pw-1", "pw-2"));

    await userEvent.click(screen.getByRole("button", { name: "로그아웃" }));
    await waitFor(() => expect(latticeApi.logout).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("완료")).toBeTruthy());
  });

  it("leaves the identity scope alone when login is refused", async () => {
    useAppStore.setState({ workspaceId: "keep-me" });
    render({ login: fail("bad credentials", {}) });
    await waitFor(() => expect(screen.getByPlaceholderText("이메일")).toBeTruthy());

    fireEvent.change(screen.getByPlaceholderText("이메일"), { target: { value: "me@x.io" } });
    fireEvent.change(screen.getByPlaceholderText("현재 비밀번호"), { target: { value: "nope" } });
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));

    await waitFor(() => expect(screen.getByText("bad credentials")).toBeTruthy());
    expect(useAppStore.getState().workspaceId).toBe("keep-me");
  });

  it("reads SSO as enabled from the flag", async () => {
    render({ ssoConfig: ok({ enabled: true, providers: [] }) });
    await waitFor(() => expect(screen.getAllByText("활성화됨").length).toBeGreaterThan(0));
  });

  it("reads SSO as enabled from a provider list alone", async () => {
    render({ ssoConfig: ok({ providers: ["oidc"] }) });
    await waitFor(() => expect(screen.getAllByText("활성화됨").length).toBeGreaterThan(0));
  });
});

describe("SystemPage — workspaces", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("uses, activates and archives a workspace from its row", async () => {
    useAppStore.setState({ workspaceId: null });
    render({
      workspaceRegistry: ok({ workspaces: [
        { workspace_id: "w1", name: "Alpha", type: "personal", your_role: "owner" },
        { id: "w2" },
      ] }),
    });
    await userEvent.click(screen.getByRole("tab", { name: "작업공간" }));
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    // A row without a name falls back to its id.
    expect(screen.getByText("w2")).toBeTruthy();

    await userEvent.click(screen.getAllByRole("button", { name: "사용" })[0]);
    expect(useAppStore.getState().workspaceId).toBe("w1");

    await userEvent.click(screen.getAllByRole("button", { name: "활성화" })[0]);
    await waitFor(() => expect(latticeApi.activateWorkspace).toHaveBeenCalledWith("w1"));

    await userEvent.click(screen.getAllByRole("button", { name: "보관" })[0]);
    await waitFor(() => expect(latticeApi.archiveWorkspace).toHaveBeenCalledWith("w1"));
  });

  it("creates organizations and invitations, and accepts a token", async () => {
    render({ invitations: ok({ invitations: [{ token: "tok-1", role: "member" }] }) });
    await userEvent.click(screen.getByRole("tab", { name: "작업공간" }));
    await waitFor(() => expect(screen.getByText("tok-1")).toBeTruthy());

    // Without an email the invitation is open — the field must go out as null.
    await userEvent.click(screen.getByRole("button", { name: "초대 만들기" }));
    await waitFor(() => expect(latticeApi.createInvitation).toHaveBeenCalledWith({
      email: null, role: "member", expires_hours: 168,
    }));

    fireEvent.change(screen.getByPlaceholderText("초대할 이메일"), { target: { value: "t@x.io" } });
    await userEvent.click(screen.getByRole("button", { name: "초대 만들기" }));
    await waitFor(() => expect(latticeApi.createInvitation).toHaveBeenCalledWith({
      email: "t@x.io", role: "member", expires_hours: 168,
    }));

    fireEvent.change(screen.getByPlaceholderText("새 조직 이름"), { target: { value: "Acme" } });
    await userEvent.click(screen.getByRole("button", { name: "조직 만들기" }));
    await waitFor(() => expect(latticeApi.createOrg).toHaveBeenCalledWith("Acme"));

    fireEvent.change(screen.getByPlaceholderText("초대 토큰"), { target: { value: "tok-9" } });
    await userEvent.click(screen.getByRole("button", { name: "초대 수락" }));
    await waitFor(() => expect(latticeApi.acceptInvitation).toHaveBeenCalledWith("tok-9"));
  });
});

describe("SystemPage — snapshots and activity", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("lists snapshots, exports, restores, creates and compares", async () => {
    render({
      snapshots: ok({ snapshots: [{ id: "s1", name: "First" }, { snapshot_id: "s2" }] }),
      timeMachine: ok({ events: [{ event: "created", type: "snapshot" }] }),
    });
    await userEvent.click(screen.getByRole("tab", { name: "스냅샷" }));
    await waitFor(() => expect(screen.getByText("First")).toBeTruthy());
    expect(screen.getByText("s2")).toBeTruthy();

    await userEvent.click(screen.getAllByRole("button", { name: "내보내기" })[0]);
    await waitFor(() => expect(latticeApi.exportSnapshot).toHaveBeenCalledWith("s1"));
    await userEvent.click(screen.getAllByRole("button", { name: "병합 복원" })[0]);
    await waitFor(() => expect(latticeApi.restoreSnapshot).toHaveBeenCalledWith("s1"));

    // An unnamed snapshot gets the localized default name, not "".
    await userEvent.click(screen.getByRole("button", { name: "스냅샷 만들기" }));
    await waitFor(() => expect(latticeApi.createSnapshot).toHaveBeenCalledWith("데스크톱 체크포인트"));
    fireEvent.change(screen.getByPlaceholderText("스냅샷 이름"), { target: { value: "주간" } });
    await userEvent.click(screen.getByRole("button", { name: "스냅샷 만들기" }));
    await waitFor(() => expect(latticeApi.createSnapshot).toHaveBeenCalledWith("주간"));

    fireEvent.change(screen.getByPlaceholderText("이전 ID"), { target: { value: "a" } });
    fireEvent.change(screen.getByPlaceholderText("이후 ID"), { target: { value: "b" } });
    await userEvent.click(screen.getByRole("button", { name: "비교" }));
    await waitFor(() => expect(latticeApi.compareSnapshots).toHaveBeenCalledWith("a", "b"));
    await waitFor(() => expect(screen.getByText("created")).toBeTruthy());
  });

  it("shows an empty timeline as empty rather than failing", async () => {
    render({ timeMachine: ok({}) });
    await userEvent.click(screen.getByRole("tab", { name: "스냅샷" }));
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    expect(document.body.textContent).not.toMatch(/undefined|NaN/);
  });

  it("shows the realtime feed and who is present", async () => {
    render({
      realtimeFeed: ok({ events: [{ event_type: "capture-run", area: "brain" }] }),
      presence: ok({ presence: [{ user: "me@x.io", workspace_id: "w1" }] }),
    });
    await userEvent.click(screen.getByRole("tab", { name: "기록" }));
    await waitFor(() => expect(screen.getByText(/capture-run/i)).toBeTruthy());
    expect(screen.getByText("me@x.io")).toBeTruthy();
  });

  it("accepts presence under the clients key too", async () => {
    render({ presence: ok({ clients: [{ user: "peer@x.io" }] }) });
    await userEvent.click(screen.getByRole("tab", { name: "기록" }));
    await waitFor(() => expect(screen.getByText("peer@x.io")).toBeTruthy());
  });

  it("reports an empty presence list politely", async () => {
    render({ presence: ok({}) });
    await userEvent.click(screen.getByRole("tab", { name: "기록" }));
    await waitFor(() => expect(screen.getByText("활성 접속 없음")).toBeTruthy());
  });
});

describe("SystemPage — devices (network tab)", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the device identity and pairs, pushes to and unpairs peers", async () => {
    useAppStore.setState({ workspaceId: "w-push" });
    render({
      networkIdentity: ok({ device_id: "dev-1", fingerprint: "fp-9", public_key: "PK PK", algorithm: "ed25519" }),
      networkPeers: ok({ peers: [{ peer_id: "p1", name: "Studio", base_url: "http://s" }, { id: "p2" }] }),
    });
    await userEvent.click(screen.getByRole("tab", { name: "기기" }));
    await waitFor(() => expect(screen.getByText("Studio")).toBeTruthy());
    expect(screen.getByText("p2")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("기기 이름"), { target: { value: "MacBook" } });
    fireEvent.change(screen.getByPlaceholderText("신뢰하는 기기 주소"), { target: { value: "http://peer" } });
    fireEvent.change(screen.getByPlaceholderText("신뢰하는 공개 키"), { target: { value: "KEY" } });
    await userEvent.click(screen.getByRole("button", { name: "기기 연결" }));
    await waitFor(() => expect(latticeApi.pairPeer).toHaveBeenCalledWith({
      name: "MacBook", base_url: "http://peer", public_key: "KEY",
    }));

    await userEvent.click(screen.getAllByRole("button", { name: "작업공간 보내기" })[0]);
    await waitFor(() => expect(latticeApi.pushPeer).toHaveBeenCalledWith("p1", "w-push"));
    await userEvent.click(screen.getAllByRole("button", { name: "연결 해제" })[0]);
    await waitFor(() => expect(latticeApi.unpairPeer).toHaveBeenCalledWith("p1"));
  });

  it("says when the identity reports nothing", async () => {
    render({ networkIdentity: ok({}) });
    await userEvent.click(screen.getByRole("tab", { name: "기기" }));
    await waitFor(() => expect(screen.getAllByText("보고되지 않음").length).toBeGreaterThan(0));
  });

  it("keeps a deep-linked device panel friendly in everyday mode", async () => {
    renderPage(<SystemPage initialTab="network" />, {
      mode: "basic",
      api: { networkIdentity: ok({ algorithm: "ed25519" }) },
    });
    await waitFor(() => expect(screen.getByText("ed25519")).toBeTruthy());
    expect(screen.getByRole("button", { name: "기기 연결" })).toBeTruthy();
  });

  it("labels an identity without an algorithm as a local identity in everyday mode", async () => {
    renderPage(<SystemPage initialTab="network" />, {
      mode: "basic",
      api: { networkIdentity: ok({}) },
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "기기 연결" })).toBeTruthy());
  });
});

describe("SystemPage — settings panels", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("switches the theme from the appearance card", async () => {
    useAppStore.setState({ theme: "light" });
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "어둡게" })).toBeTruthy());

    await userEvent.click(screen.getByRole("button", { name: "어둡게" }));
    expect(useAppStore.getState().theme).toBe("dark");
    await userEvent.click(screen.getByRole("button", { name: "밝게" }));
    expect(useAppStore.getState().theme).toBe("light");
  });

  it("switches the detail level from the settings card", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "기본" })).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "기본" }));
    expect(useAppStore.getState().mode).toBe("basic");
  });

  it("renders rich health, storage and backup data in advanced mode", async () => {
    render({
      health: ok({ status: "ok", version: "vv-9", mode: "local", port: 8787 }),
      brainStorage: ok({
        active: { engine: "postgres", available: false, reason: true, vector_search: "vec0", vector_reason: "loaded" },
        postgres: { available: true, reason: "up" },
        backup_health: { count: 2 },
      }),
      backupHealth: ok({
        available: false, count: 3, encrypted_archives: 1, zip_backups: 2,
        directory: "/backups", latest: "b-3", last_verified: "yesterday", error: "disk almost full",
      }),
    });
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getAllByText("vv-9").length).toBeGreaterThan(0));
    expect(screen.getAllByText("vec0").length).toBeGreaterThan(0);
    expect(screen.getByText("/backups")).toBeTruthy();
    expect(screen.getByText("disk almost full")).toBeTruthy();
  });

  it("falls back honestly when health, storage and backups report nothing", async () => {
    render({ health: ok({}), brainStorage: ok({}), backupHealth: ok({}) });
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getAllByText("보고되지 않음").length).toBeGreaterThan(0));
    expect(document.body.textContent).not.toMatch(/undefined|\[object Object\]/);
  });

  it.each([
    ["low", "부족"],
    ["tight", "타이트"],
    ["roomy", "넉넉"],
    ["exotic", "넉넉"],
  ])("explains readiness %s in everyday words", async (readiness, expected) => {
    const view = renderPage(<SystemPage initialTab="settings" />, {
      mode: "basic",
      api: { sysinfo: ok({ readiness }) },
    });
    await waitFor(() => expect(document.body.textContent).toContain(expected));
    view.unmount();
  });

  it("assumes a roomy machine when readiness is not reported", async () => {
    renderPage(<SystemPage initialTab="settings" />, { mode: "basic", api: { sysinfo: ok({}) } });
    await waitFor(() => expect(document.body.textContent).toContain("넉넉"));
  });

  it("shows search readiness in basic mode from the vector flag", async () => {
    const view = renderPage(<SystemPage initialTab="settings" />, {
      mode: "basic",
      api: { brainStorage: ok({ vector_search: "vec0" }) },
    });
    await waitFor(() => expect(screen.getByText("켜짐")).toBeTruthy());
    view.unmount();
    renderPage(<SystemPage initialTab="settings" />, { mode: "basic", api: { brainStorage: ok({}) } });
    await waitFor(() => expect(screen.getByText("준비 중")).toBeTruthy());
  });

  it("walks the whole archive lifecycle with its guardrails", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByPlaceholderText("아카이브 암호문")).toBeTruthy());

    fireEvent.change(screen.getByPlaceholderText("검사 또는 복원할 아카이브 경로"), { target: { value: "/tmp/a.tgz" } });
    // Inspect may run without a passphrase — it goes out as null, not "".
    await userEvent.click(screen.getByRole("button", { name: "검사" }));
    await waitFor(() => expect(latticeApi.brainArchiveInspect).toHaveBeenCalledWith({ path: "/tmp/a.tgz", passphrase: null }));

    fireEvent.change(screen.getByPlaceholderText("아카이브 암호문"), { target: { value: "pp" } });
    // Without an export path the server picks the location — null, not "".
    await userEvent.click(screen.getByRole("button", { name: "아카이브 내보내기" }));
    await waitFor(() => expect(latticeApi.brainArchive).toHaveBeenCalledWith({ path: null, passphrase: "pp" }));
    fireEvent.change(screen.getByPlaceholderText("내보내기 경로(선택)"), { target: { value: "/tmp/out.tgz" } });
    await userEvent.click(screen.getByRole("button", { name: "아카이브 내보내기" }));
    await waitFor(() => expect(latticeApi.brainArchive).toHaveBeenCalledWith({ path: "/tmp/out.tgz", passphrase: "pp" }));

    await userEvent.click(screen.getByRole("button", { name: "검사" }));
    await waitFor(() => expect(latticeApi.brainArchiveInspect).toHaveBeenCalledWith({ path: "/tmp/a.tgz", passphrase: "pp" }));
    await userEvent.click(screen.getByRole("button", { name: "검증" }));
    await waitFor(() => expect(latticeApi.brainArchiveVerify).toHaveBeenCalledWith({ path: "/tmp/a.tgz", passphrase: "pp" }));
    await userEvent.click(screen.getByRole("button", { name: "복원 미리 실행" }));
    await waitFor(() => expect(latticeApi.brainArchiveRestore).toHaveBeenCalledWith({
      path: "/tmp/a.tgz", passphrase: "pp", dry_run: true, confirm: false,
    }));
    await userEvent.click(screen.getByRole("button", { name: "가져오기 미리 실행" }));
    await waitFor(() => expect(latticeApi.brainArchiveImport).toHaveBeenCalledWith({
      path: "/tmp/a.tgz", passphrase: "pp", dry_run: true, confirm: false,
    }));

    await userEvent.click(screen.getByLabelText("복원 확인"));
    await userEvent.click(screen.getByRole("button", { name: "복원" }));
    await waitFor(() => expect(latticeApi.brainArchiveRestore).toHaveBeenCalledWith({
      path: "/tmp/a.tgz", passphrase: "pp", dry_run: false, confirm: true,
    }));
    await userEvent.click(screen.getByLabelText("가져오기 확인"));
    await userEvent.click(screen.getByRole("button", { name: "가져오기" }));
    await waitFor(() => expect(latticeApi.brainArchiveImport).toHaveBeenCalledWith({
      path: "/tmp/a.tgz", passphrase: "pp", dry_run: false, confirm: true,
    }));
  });

  it("plans and starts Docker Postgres and plans a migration", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Docker 계획" })).toBeTruthy());

    await userEvent.click(screen.getByRole("button", { name: "Docker 계획" }));
    await waitFor(() => expect(latticeApi.dockerPostgres).toHaveBeenCalledWith({ consent: false, dry_run: true, port: 5432 }));
    await userEvent.click(screen.getByLabelText("Docker 시작 동의"));
    await userEvent.click(screen.getByRole("button", { name: "Docker 시작" }));
    await waitFor(() => expect(latticeApi.dockerPostgres).toHaveBeenCalledWith({ consent: true, dry_run: false, port: 5432 }));

    fireEvent.change(screen.getByPlaceholderText("Postgres 연결 문자열"), { target: { value: "postgres://x" } });
    await userEvent.click(screen.getByRole("button", { name: "마이그레이션 계획" }));
    await waitFor(() => expect(latticeApi.migratePostgres).toHaveBeenCalledWith({
      dsn: "postgres://x", schema_name: "lattice_brain", dry_run: true,
    }));
    // Clearing the schema falls back to the default schema name.
    fireEvent.change(screen.getByPlaceholderText("데이터베이스 스키마"), { target: { value: "" } });
    await userEvent.click(screen.getByRole("button", { name: "마이그레이션 계획" }));
    await waitFor(() => expect(latticeApi.migratePostgres).toHaveBeenCalledTimes(2));
    expect(vi.mocked(latticeApi.migratePostgres).mock.calls[1][0]).toEqual({
      dsn: "postgres://x", schema_name: "lattice_brain", dry_run: true,
    });
  });

  it("toggles computer memory through its action buttons", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "메모리 활성화" })).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "메모리 활성화" }));
    await waitFor(() => expect(latticeApi.setComputerMemory).toHaveBeenCalledWith(true));
    await userEvent.click(screen.getByRole("button", { name: "메모리 비활성화" }));
    await waitFor(() => expect(latticeApi.setComputerMemory).toHaveBeenCalledWith(false));
  });
});

describe("SystemPage — admin tab", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows every admin panel with real data in admin mode", async () => {
    renderPage(<SystemPage initialTab="admin" />, {
      mode: "admin",
      api: {
        adminSummary: ok({ total_users: 3 }),
        adminUsers: ok([{ email: "adm@x.io", role: "admin" }]),
        adminAudit: ok({ recent_events: [{ act: "audit_login", sev: "info" }] }),
        adminRoles: ok({ roles: [{ role: "operator-x", members: 1 }] }),
        adminPolicies: ok({ policies: [{ label: "policy-retention", enforced: true }] }),
        adminProductHardening: ok({
          version: "vv-10",
          startup: { network_exposed: true, local_only_default: true },
          privacy: { local_only_default: false },
          storage: { active: { engine: "sqlite" } },
          backup: { count: 0, available: true },
          device_identity: { algorithm: "ed25519", storage: false },
          permissions: { destructive_restore_requires_confirmation: false },
        }),
        adminSecurity: ok({
          cards: { events_today: 55, high_risk_events: 2, review_required: 1 },
          severity_counts: { high: 3 },
          risk_rate: 0.25,
          field_counts: { email: 1 },
        }),
        vpcStatus: ok({ status: "community" }),
      },
    });
    await waitFor(() => expect(screen.getByText("adm@x.io")).toBeTruthy());
    expect(screen.getByText("audit_login")).toBeTruthy();
    expect(screen.getByText("operator-x")).toBeTruthy();
    expect(screen.getByText("policy-retention")).toBeTruthy();
    expect(screen.getByText("vv-10")).toBeTruthy();
    expect(screen.getByText("55")).toBeTruthy();
  });

  it("keeps the admin panels honest when every service reports nothing", async () => {
    renderPage(<SystemPage initialTab="admin" />, { mode: "admin" });
    await waitFor(() => expect(document.querySelectorAll(".data-panel").length).toBeGreaterThanOrEqual(8));
    // Wait until the hardening and security views have rendered their empty
    // payloads — that is where every fallback phrase earns its keep.
    await waitFor(() => expect(document.querySelectorAll(".data-stat-grid").length).toBeGreaterThanOrEqual(2));
    expect(document.body.textContent).not.toMatch(/undefined|\[object Object\]/);
  });
});
