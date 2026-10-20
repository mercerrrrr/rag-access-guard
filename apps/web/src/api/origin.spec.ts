import { expect, it } from "vitest";
import { sourceSchema, sourceContentSchema } from "./types";
import { source } from "@/test/chat";

const origin = { kind: "official_public", publisher: "Издатель", source_url: "https://example.org/doc",
  retrieved_at: "2026-10-21T00:00:00Z", published_on: "2015-01-01", source_sha256: "a".repeat(64),
  transformation_revision: "v1" } as const;

it("retains_authorized_publication_origin_in_server_sources", () => {
  expect(sourceSchema.parse({ ...source, origin })).toHaveProperty("origin", origin);
});

it("rejects_open_source_without_a_current_policy_revision", () => {
  expect(sourceContentSchema.safeParse({ ...source, text: "текст", origin }).success).toBe(false);
});

it("retains_current_revision_without_using_thread_revision", () => {
  expect(sourceContentSchema.parse({ ...source, text: "текст", origin, policy_revision: 42 }))
    .toHaveProperty("policy_revision", 42);
});

it.each([-1, 1.5, "42"])("rejects_invalid_policy_revision %s", policy_revision => {
  expect(sourceContentSchema.safeParse({ ...source, text: "текст", origin, policy_revision }).success).toBe(false);
});
