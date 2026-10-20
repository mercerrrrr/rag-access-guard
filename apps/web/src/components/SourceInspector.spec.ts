import { expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import SourceInspector from "./SourceInspector.vue";
import { ApiError } from "@/api/errors";
import type { SourceContent } from "@/api/types";
import { createSourceInspector } from "@/composables/useSourceInspector";
import { createSessionState } from "@/composables/useSession";
import { chatApi, source } from "@/test/chat";
import { deferred, sessionApi } from "@/test/session";

it("source_404_clears_inspector", async () => {
  let denied = false;
  const session = createSessionState(sessionApi());
  await session.refresh();
  const inspector = createSourceInspector(chatApi({ source: () => denied
    ? Promise.reject(new ApiError(404)) : Promise.resolve({ ...source, text: "private", policy_revision: 42 }) }), session);
  await inspector.open(source);
  expect(inspector.content.value?.text).toBe("private");
  denied = true;
  await inspector.open(source);
  expect(inspector.content.value).toBeNull();
  expect(inspector.selected.value).toBeNull();
});

it("late_source_after_clear_cannot_restore_content", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const pending = deferred<SourceContent>();
  const inspector = createSourceInspector(chatApi({ source: () => pending.promise }), session);
  const request = inspector.open(source);
  inspector.clear();
  pending.resolve({ ...source, text: "private", policy_revision: 42 });
  await request;
  expect(inspector.content.value).toBeNull();
});

it("source_content_must_match_all_three_refs", async () => {
  const session = createSessionState(sessionApi());
  await session.refresh();
  const inspector = createSourceInspector(chatApi({ source: () => Promise.resolve({
    ...source, chunk_id: "00000000-0000-4000-8000-000000000004", text: "wrong chunk", policy_revision: 42,
  }) }), session);
  await inspector.open(source);
  expect(inspector.content.value).toBeNull();
});

it("shows_current_read_revision_and_removes_metadata_when_cleared", async () => {
  const wrapper = mount(SourceInspector, { props: { busy: false, message: "", content: {
    ...source, text: "PRIVATE_SOURCE", policy_revision: 42, origin: {
      kind: "synthetic_demo", publisher: "Учебный демонстрационный корпус", source_url: null,
      published_on: null, retrieved_at: null, source_sha256: "a".repeat(64), transformation_revision: "v1",
    },
  } } });
  expect(wrapper.text()).toContain("Проверено по ревизии 42");
  expect(wrapper.text()).toContain("Вымышленный учебный пример, не документ МЭИ.");
  expect(wrapper.get("details").text()).toContain(source.document_version_id);
  await wrapper.setProps({ content: null });
  expect(wrapper.html()).not.toContain("PRIVATE_SOURCE");
  expect(wrapper.html()).not.toContain("Учебный демонстрационный корпус");
  expect(wrapper.html()).not.toContain("42");
  expect(wrapper.find("details, button").exists()).toBe(false);
  wrapper.unmount();
});
