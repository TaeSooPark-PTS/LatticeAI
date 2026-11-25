import { afterEach, describe, expect, it } from "vitest";

import { focusComposer, navigateHash } from "./navigation";

describe("navigation helpers", () => {
  afterEach(() => {
    window.location.hash = "";
    document.body.innerHTML = "";
  });

  it("navigateHash writes the route into the location hash", () => {
    navigateHash("/models");
    expect(window.location.hash).toBe("#/models");
  });

  it("focusComposer is a no-op when no composer is mounted", () => {
    expect(document.querySelector(".brain-composer textarea")).toBeNull();
    expect(() => focusComposer()).not.toThrow();
  });

  it("focusComposer focuses the composer textarea when present", () => {
    const wrapper = document.createElement("div");
    wrapper.className = "brain-composer";
    const textarea = document.createElement("textarea");
    wrapper.appendChild(textarea);
    document.body.appendChild(wrapper);

    focusComposer();
    expect(document.activeElement).toBe(textarea);
  });
});
