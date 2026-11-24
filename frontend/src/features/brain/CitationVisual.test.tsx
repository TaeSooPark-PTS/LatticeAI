import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { CitationVisual, isLocalImageLocator } from "./AnswerProof";
import { buildBrainProof } from "./brainData";
import type { Message } from "./types";

type Citation = NonNullable<Message["proof"]>["citations"][number];

const PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGP4DwABAQEAWk1v8QAAAABJRU5ErkJggg==";

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    id: "image:abc",
    source: "그래프",
    title: "whiteboard.png",
    snippet: "Q3 roadmap",
    matchedTerms: ["roadmap"],
    confidence: "high",
    score: 0.8,
    locator: "",
    ...overrides,
  };
}

describe("multi-modal evidence", () => {
  it("shows nothing extra for an ordinary text citation", () => {
    render(<CitationVisual language="ko" citation={citation({ kind: "Document" })} />);

    expect(screen.queryByTestId("citation-visual")).toBeNull();
  });

  it("treats a citation with no kind at all as text", () => {
    render(<CitationVisual language="ko" citation={citation()} />);

    expect(screen.queryByTestId("citation-visual")).toBeNull();
  });

  it("renders the thumbnail and the caption a vision model wrote", () => {
    render(
      <CitationVisual
        language="ko"
        citation={citation({
          kind: "Image",
          caption: "회의실에서 찍은 화이트보드",
          thumbnail: PIXEL,
        })}
      />,
    );

    const image = screen.getByRole("img");
    expect(image).toHaveAttribute("src", PIXEL);
    // The caption doubles as the alt text — it is the honest description.
    expect(image).toHaveAttribute("alt", "회의실에서 찍은 화이트보드");
    expect(screen.getByTestId("citation-caption")).toHaveTextContent(
      "회의실에서 찍은 화이트보드",
    );
  });

  it("says plainly that there is no caption when no vision model ran", () => {
    render(
      <CitationVisual language="ko" citation={citation({ kind: "Image", thumbnail: PIXEL })} />,
    );

    expect(screen.getByTestId("citation-caption-absent")).toHaveTextContent(
      "비전 모델이 없어",
    );
    // Falls back to the filename for alt text rather than inventing a description.
    expect(screen.getByRole("img")).toHaveAttribute("alt", "whiteboard.png 이미지 미리보기");
  });

  it("falls back to a labelled badge when the node carries no thumbnail", () => {
    render(
      <CitationVisual
        language="en"
        citation={citation({ kind: "ImageText", caption: "A scanned receipt" })}
      />,
    );

    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByTestId("citation-visual")).toHaveTextContent("Image");
    expect(screen.getByTestId("citation-caption")).toHaveTextContent("A scanned receipt");
  });
});

describe("evidence payload parsing", () => {
  it("carries kind, caption and thumbnail through from a recall row", () => {
    const proof = buildBrainProof({
      recall: {
        query: "화이트보드",
        count: 1,
        items: [
          {
            id: "image:abc",
            source: "graph",
            title: "whiteboard.png",
            snippet: "Q3 roadmap",
            score: 0.8,
            kind: "Image",
            caption: "A whiteboard",
            thumbnail: PIXEL,
          },
        ],
      },
    });

    expect(proof.recall.items[0]!.kind).toBe("Image");
    expect(proof.recall.items[0]!.caption).toBe("A whiteboard");
    expect(proof.recall.items[0]!.thumbnail).toBe(PIXEL);
  });

  it("drops a thumbnail that is not an inline image", () => {
    const proof = buildBrainProof({
      recall: {
        query: "q",
        count: 2,
        items: [
          { id: "a", thumbnail: "https://tracker.example/pixel.gif" },
          { id: "b", thumbnail: 42 },
        ],
      },
    });

    // A remote src would make an evidence card phone home; a non-string is junk.
    expect(proof.recall.items[0]!.thumbnail).toBe("");
    expect(proof.recall.items[1]!.thumbnail).toBe("");
  });
});

describe("consent-gated original image serving", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("recognizes local raster paths and rejects remote or non-image locators", () => {
    expect(isLocalImageLocator("")).toBe(false);
    expect(isLocalImageLocator("https://example.com/a.png")).toBe(false);
    expect(isLocalImageLocator("data:image/png;base64,AAA")).toBe(false);
    expect(isLocalImageLocator("/tmp/notes.md")).toBe(false);
    expect(isLocalImageLocator("/Users/alice/whiteboard.webp")).toBe(true);
  });

  it("requests approval on click, displays /local/serve image, and closes on second click", async () => {
    let resolveServe: (v: unknown) => void = () => {};
    vi.spyOn(latticeApi, "resolveApprovedLocalServeUrl").mockReturnValueOnce(
      new Promise((resolve) => {
        resolveServe = resolve;
      }) as never,
    );

    render(
      <CitationVisual
        language="ko"
        citation={citation({
          kind: "Image",
          caption: "다이어그램",
          thumbnail: PIXEL,
          locator: "/tmp/diagram.png",
        })}
      />,
    );

    const btn = screen.getByTestId("citation-open-original");
    expect(btn).toHaveTextContent("원본 보기");
    fireEvent.click(btn);
    expect(screen.getByTestId("citation-open-original")).toHaveTextContent("승인 확인 중…");

    resolveServe({
      ok: true,
      status: 200,
      source: "live",
      data: { url: "/local/serve?path=%2Ftmp%2Fdiagram.png&approval_token=t1" },
    });

    const fullImg = await screen.findByTestId("citation-original-image");
    expect(fullImg).toHaveAttribute("src", "/local/serve?path=%2Ftmp%2Fdiagram.png&approval_token=t1");
    expect(fullImg).toHaveAttribute("alt", "다이어그램");
    expect(screen.getByTestId("citation-open-original")).toHaveTextContent("원본 닫기");

    // Fallback alt when caption is absent
    fireEvent.click(screen.getByTestId("citation-open-original"));
    await waitFor(() => expect(screen.queryByTestId("citation-original-image")).toBeNull());
  });

  it("shows localized error message and fallback alt when approval or caption is missing", async () => {
    vi.spyOn(latticeApi, "resolveApprovedLocalServeUrl")
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        source: "unavailable",
        data: { url: "" },
        error: "denied",
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: { url: "/local/serve?path=%2Ftmp%2Fplain.jpg&approval_token=t2" },
      });

    render(
      <CitationVisual
        language="en"
        citation={citation({
          kind: "Image",
          locator: "/tmp/plain.jpg",
        })}
      />,
    );

    fireEvent.click(screen.getByTestId("citation-open-original"));
    expect(await screen.findByTestId("citation-original-error")).toHaveTextContent(
      "Could not load original image.",
    );

    fireEvent.click(screen.getByTestId("citation-open-original"));
    const fullImg = await screen.findByTestId("citation-original-image");
    expect(fullImg).toHaveAttribute("alt", "Preview of whiteboard.png");
  });
});
