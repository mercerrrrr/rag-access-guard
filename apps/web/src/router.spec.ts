import { createMemoryHistory } from "vue-router";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/router";

describe("application routes", () => {
  it("redirects the root path and exposes each primary section", async () => {
    const router = createAppRouter(createMemoryHistory());

    await router.push("/");
    await router.isReady();

    expect(router.currentRoute.value.path).toBe("/chat");
    expect(router.getRoutes().map((route) => route.path)).toEqual(
      expect.arrayContaining(["/chat", "/documents", "/access", "/audit"]),
    );
  });
});
