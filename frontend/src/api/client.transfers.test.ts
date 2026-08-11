import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "./client";
import { failFetchWith, jsonResponse, recordFetch, resetDispatcher } from "@/test/apiClientHarness";

/**
 * The wrappers that move a file or a decision rather than read a list: uploads,
 * downloads, approval resume, the demo corpus, and the agent run whose failure
 * is a throw rather than an envelope.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

afterEach(resetDispatcher);

describe("uploadDocument", () => {
  const file = () => new File(["안녕"], "메모.txt", { type: "text/plain" });

  it("posts the file as multipart form data", async () => {
    const calls = recordFetch(() => jsonResponse({ status: "ingested" }));

    const res = await latticeApi.uploadDocument(file());

    expect(res.ok).toBe(true);
    expect(res.data).toEqual({ status: "ingested" });
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url.pathname).toBe("/upload/document");
    const body = calls[0].body as FormData;
    expect(body).toBeInstanceOf(FormData);
    const part = body.get("file") as File;
    expect(part.name).toBe("메모.txt");
  });

  it("reads the server's explanation out of a rejected upload", async () => {
    recordFetch(() => jsonResponse({ detail: "너무 큽니다" }, 413));

    const res = await latticeApi.uploadDocument(file());

    expect(res.ok).toBe(false);
    expect(res.status).toBe(413);
    expect(res.error).toBe("너무 큽니다");
  });

  it("falls back to a generic message when the failure body is not JSON", async () => {
    recordFetch(() => new Response("boom", { status: 500, statusText: "", headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.uploadDocument(file());

    expect(res.ok).toBe(false);
    expect(res.data).toBeNull();
    expect(res.error).toBe("Upload failed");
  });

  it("reports a network drop without throwing", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.uploadDocument(file());

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.error).toContain("Failed to fetch");
  });
});

describe("resumeAgentApproval", () => {
  const body = { run_id: "r1", approval_token: "tok", approve: true };

  it("posts the resume request and passes the payload through", async () => {
    const calls = recordFetch(() => jsonResponse({ status: "resumed" }));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(calls[0].method).toBe("POST");
    expect(calls[0].url.pathname).toBe("/agent/resume");
    expect(calls[0].body).toEqual(body);
    expect(res.ok).toBe(true);
    expect(res.data).toEqual({ status: "resumed" });
  });

  it("normalises a non-object success payload to an empty record", async () => {
    recordFetch(() => jsonResponse("done"));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(res.ok).toBe(true);
    expect(res.data).toEqual({});
  });

  it("keeps the HTTP status so callers can tell an expired token from a lost run", async () => {
    recordFetch(() => jsonResponse({ detail: { user_message: "승인이 만료되었어요" } }, 410));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(res.ok).toBe(false);
    expect(res.status).toBe(410);
    expect(res.error).toBe("승인이 만료되었어요");
  });

  it("labels a bodiless failure with its HTTP status", async () => {
    recordFetch(() => new Response("nope", { status: 500, statusText: "", headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(res.ok).toBe(false);
    expect(res.error).toBe("HTTP 500");
  });

  it("reports a network drop as unreachable", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.resumeAgentApproval(body);

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data).toEqual({});
    expect(res.error).toBeTruthy();
  });
});

describe("installDemoCorpus", () => {
  it("merges the install report over the declared shape", async () => {
    const calls = recordFetch(() => jsonResponse({ status: "installed", ingested: 3 }));

    const res = await latticeApi.installDemoCorpus();

    expect(calls[0].method).toBe("POST");
    expect(calls[0].url.pathname).toBe("/api/setup/demo-corpus");
    expect(res.ok).toBe(true);
    expect(res.data).toMatchObject({ status: "installed", ingested: 3, duplicates: 0, documents: [] });
  });

  it("keeps the empty shape when the success body is not an object", async () => {
    recordFetch(() => new Response("ok!", { status: 200, headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.installDemoCorpus();

    expect(res.ok).toBe(true);
    expect(res.data).toEqual({ status: "", ingested: 0, duplicates: 0, documents: [], suggested_questions: [] });
  });

  it("reports a refusal with the declared shape intact", async () => {
    recordFetch(() => new Response(null, { status: 503, statusText: "" }));

    const res = await latticeApi.installDemoCorpus();

    expect(res.ok).toBe(false);
    expect(res.error).toBe("HTTP 503");
    expect(res.data.documents).toEqual([]);
  });

  it("reports a network drop without throwing", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.installDemoCorpus();

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data.suggested_questions).toEqual([]);
  });
});

describe("readWorkspaceFile", () => {
  it("returns the file body as text", async () => {
    const calls = recordFetch(() => new Response("<h1>안녕</h1>", { status: 200 }));

    const res = await latticeApi.readWorkspaceFile("out/페이지.html");

    expect(calls[0].url.pathname).toBe("/tools/download");
    expect(calls[0].url.searchParams.get("path")).toBe("out/페이지.html");
    expect(res.ok).toBe(true);
    expect(res.data.content).toBe("<h1>안녕</h1>");
  });

  it("keeps a 404 distinguishable and its message readable", async () => {
    recordFetch(() => jsonResponse({ detail: "파일이 없습니다" }, 404));

    const res = await latticeApi.readWorkspaceFile("gone.html");

    expect(res.ok).toBe(false);
    expect(res.status).toBe(404);
    expect(res.data.content).toBe("");
    expect(res.error).toBe("파일이 없습니다");
  });

  it("labels a bodiless failure with its HTTP status", async () => {
    recordFetch(() => new Response("x", { status: 500, statusText: "", headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.readWorkspaceFile("f.txt");

    expect(res.error).toBe("HTTP 500");
  });

  it("reports a network drop as unreachable", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.readWorkspaceFile("f.txt");

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data.content).toBe("");
  });
});

describe("downloadWorkspaceFile", () => {
  let clickSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:lattice/1") as never;
    URL.revokeObjectURL = vi.fn() as never;
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("downloads through a temporary anchor named after the file", async () => {
    recordFetch(() => new Response("내용", { status: 200 }));

    const res = await latticeApi.downloadWorkspaceFile("out/보고서.md", "보고서.md");

    expect(res.ok).toBe(true);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("보고서.md");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:lattice/1");
  });

  it("names the download from the path when no filename is given", async () => {
    recordFetch(() => new Response("내용", { status: 200 }));

    await latticeApi.downloadWorkspaceFile("nested/dir/파일.txt", "");

    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("파일.txt");
  });

  it("falls back to a generic name when there is no path either", async () => {
    recordFetch(() => new Response("내용", { status: 200 }));

    await latticeApi.downloadWorkspaceFile("", "");

    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("download");
  });

  it("reports a refusal without creating an anchor", async () => {
    recordFetch(() => jsonResponse({ detail: "권한 없음" }, 403));

    const res = await latticeApi.downloadWorkspaceFile("f.txt", "f.txt");

    expect(res).toEqual({ ok: false, error: "권한 없음" });
    expect(clickSpy).not.toHaveBeenCalled();
  });

  it("falls back to a generic message when the refusal body is not JSON", async () => {
    recordFetch(() => new Response("boom", { status: 500, statusText: "", headers: { "Content-Type": "text/plain" } }));

    const res = await latticeApi.downloadWorkspaceFile("f.txt", "f.txt");

    expect(res).toEqual({ ok: false, error: "Download failed" });
  });

  it("reports a network drop without throwing", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.downloadWorkspaceFile("f.txt", "f.txt");

    expect(res.ok).toBe(false);
    expect(res.error).toBeTruthy();
  });
});

describe("runAgent", () => {
  it("resolves with the envelope when the run is accepted", async () => {
    const calls = recordFetch(() => jsonResponse({ run_id: "r1" }));

    const res = await latticeApi.runAgent("정리해줘", ["writer"]);

    expect(calls[0].url.pathname).toBe("/agents/api/run");
    expect(calls[0].body).toEqual({ goal: "정리해줘", roles: ["writer"] });
    expect(res.ok).toBe(true);
  });

  it("throws the server's message so the caller's error path runs", async () => {
    recordFetch(() => jsonResponse({ detail: "런타임이 준비되지 않았어요" }, 503));

    await expect(latticeApi.runAgent("정리해줘", [])).rejects.toThrow("런타임이 준비되지 않았어요");
  });

  it("names the HTTP status when the server offers no message at all", async () => {
    // An empty-string detail survives friendlyError verbatim, which is the one
    // way a failed envelope can carry a falsy error.
    recordFetch(() => jsonResponse({ detail: "" }, 502));

    await expect(latticeApi.runAgent("정리해줘", [])).rejects.toThrow("Agent run failed with HTTP 502");
  });
});
