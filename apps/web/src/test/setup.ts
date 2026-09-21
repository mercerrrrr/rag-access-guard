import { afterEach } from "vitest";
import { config } from "@vue/test-utils";

afterEach(() => {
  document.body.innerHTML = "";
});

config.global.renderStubDefaultSlot = true;
