import { createMemoryHistory } from "vue-router";
import { describe, expect, it } from "vitest";

import { createAppRouter } from "@/router";
import { safeReturnPath } from "@/routePaths";
import { createSessionState } from "@/composables/useSession";
import { sessionApi } from "@/test/session";
import { ApiError } from "@/api/errors";

describe("application routes", () => {
  it("redirects the root path and exposes each primary section", async () => {
    const router = createAppRouter(createMemoryHistory(), createSessionState(sessionApi()));

    await router.push("/");
    await router.isReady();

    expect(router.currentRoute.value.path).toBe("/chat");
    expect(router.getRoutes().map((route) => route.path)).toEqual(
      expect.arrayContaining(["/chat", "/documents", "/access", "/audit"]),
    );
  });
});

it.each(["https://foreign.example", "//foreign.example", "/\\foreign.example", "/login", "/chat?next=https://foreign.example", ["/chat"], "/unknown"])("external_return_url_is_rejected: %s", (path) => {
    expect(safeReturnPath(path)).toBe("/chat");
  });

it("anonymous_navigation_requires_login", async () => {
  const state = createSessionState(sessionApi({ me: () => Promise.reject(new ApiError(401)) }));
  const router = createAppRouter(createMemoryHistory(), state);
  await router.push("/documents");
  expect(router.currentRoute.value.path).toBe("/login");
  expect(router.currentRoute.value.query["returnTo"]).toBe("/documents");
});

it("non_admin_cannot_open_administrative_routes", async () => {
  const state = createSessionState(sessionApi());
  const router = createAppRouter(createMemoryHistory(), state);
  await router.push("/audit");
  expect(router.currentRoute.value.path).toBe("/chat");
});
