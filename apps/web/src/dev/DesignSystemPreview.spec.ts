import "@fluentui/web-components/button.js";

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import DesignSystemPreview from "@/dev/DesignSystemPreview.vue";

describe("design system preview", () => {
  it("uses supported Fluent button appearances and states", async () => {
    await customElements.whenDefined("fluent-button");

    const wrapper = mount(DesignSystemPreview);
    const [primaryButton, outlineButton, disabledButton] = wrapper
      .findAll("fluent-button")
      .map(
        (button) =>
          button.element as HTMLElement & {
            appearance?: string;
            disabled: boolean;
          },
      );

    expect(primaryButton?.appearance).toBe("primary");
    expect(outlineButton?.appearance).toBe("outline");
    expect(disabledButton?.disabled).toBe(true);
    expect(disabledButton?.hasAttribute("tabindex")).toBe(false);
    expect(
      [primaryButton, outlineButton, disabledButton].some(
        (button) => button?.appearance === "accent",
      ),
    ).toBe(false);
  });
});
