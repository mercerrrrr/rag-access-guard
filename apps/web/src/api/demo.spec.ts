import { afterEach, expect, it, vi } from "vitest";
import { demoApi } from "@/api/demo";

const payloads = {
  suggestions: { items: [{ id: "first", question: "Какие правила?" }], policy_revision: 2 },
  profile: { enabled: false, title: "Помощник по базе знаний", subtitle: null,
    notice: "Исследовательский прототип", logo_url: null },
  status: { mode: "local", model: { name: "audited", state: "ready" }, search: { state: "ready" } },
} as const;
afterEach(() => vi.unstubAllGlobals());

it.each(["suggestions", "profile", "status"] as const)("reads_strict_%s_from_its_fixed_endpoint", async key => {
  let sent: Request | undefined;
  vi.stubGlobal("fetch", (request: Request) => { sent = request; return Promise.resolve(Response.json(payloads[key])); });
  await expect(demoApi[key](new AbortController().signal)).resolves.toEqual(payloads[key]);
  expect(new URL(sent?.url ?? "http://missing").pathname).toBe({
    suggestions: "/api/chat/suggestions", profile: "/api/demo/profile", status: "/api/system/status",
  }[key]);
  expect(sent?.cache).toBe("no-store"); expect(sent?.credentials).toBe("same-origin");
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ ...payloads[key], secret: "closed" })));
  await expect(demoApi[key](new AbortController().signal)).rejects.toMatchObject({ status: 0 });
});

it.each([
  { items: Array.from({ length: 5 }, () => payloads.suggestions.items[0]), policy_revision: 2 },
  { ...payloads.suggestions, policy_revision: -1 }, { ...payloads.suggestions, policy_revision: 1.5 },
  { items: [{ id: "first", question: "  " }], policy_revision: 2 },
  { items: [{ id: "bad/id", question: "question" }], policy_revision: 2 },
  { items: [{ id: "x".repeat(101), question: "question" }], policy_revision: 2 },
  { items: [{ id: "first", question: "x".repeat(4097) }], policy_revision: 2 },
  { items: [{ ...payloads.suggestions.items[0], title: "CLOSED_TITLE" }], policy_revision: 2 },
])("rejects_untrusted_suggestion_metadata_%j", async body => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json(body)));
  await expect(demoApi.suggestions(new AbortController().signal)).rejects.toMatchObject({ status: 0 });
});

it.each([{ enabled: true }, { title: "Other brand" }, { subtitle: "Institution" },
  { notice: "Official deployment" }, { logo_url: "/api/demo/brand/logo" }])("rejects_unimplemented_profile_%j", async patch => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ ...payloads.profile, ...patch })));
  await expect(demoApi.profile(new AbortController().signal)).rejects.toMatchObject({ status: 0 });
});

it.each([{ mode: "production" }, { model: { name: null, state: "healthy" } },
  { search: { state: "test" } }, { model: { ...payloads.status.model, secret: "private" } }])("rejects_unknown_status_%j", async patch => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ ...payloads.status, ...patch })));
  await expect(demoApi.status(new AbortController().signal)).rejects.toMatchObject({ status: 0 });
});

it.each([401, 403, 503])("preserves_typed_failure_%s", async status => {
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json({ detail: "safe" }, { status })));
  await expect(demoApi.suggestions(new AbortController().signal)).rejects.toMatchObject({ status });
});

it("preserves_exact_question_text_and_the_servers_unicode_character_limit", async () => {
  const question = "\u{1d400}".repeat(4096);
  const body = { items: [{ id: "unicode", question }], policy_revision: 2 };
  vi.stubGlobal("fetch", () => Promise.resolve(Response.json(body)));
  await expect(demoApi.suggestions(new AbortController().signal)).resolves.toEqual(body);
});
