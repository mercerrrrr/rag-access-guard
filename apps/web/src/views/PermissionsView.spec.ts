import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { accessApi } from "@/api/access";
import { sessionKey } from "@/composables/sessionContext";
import { createSessionState } from "@/composables/useSession";
import { accessRole, accessUser, directGrant, roleGrant } from "@/test/access";
import { document as adminDocument } from "@/test/documents";
import { sessionApi, student } from "@/test/session";
import PermissionsView from "@/views/PermissionsView.vue";

afterEach(() => vi.restoreAllMocks());
async function mountView(admin = true) {
  vi.spyOn(accessApi, "users").mockResolvedValue([accessUser]);
  vi.spyOn(accessApi, "roles").mockResolvedValue([accessRole]);
  vi.spyOn(accessApi, "documents").mockResolvedValue([adminDocument]);
  vi.spyOn(accessApi, "grants").mockResolvedValue([directGrant, roleGrant]);
  const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: admin } }) }));
  await session.refresh();
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/access", component: PermissionsView }] });
  await router.push(`/access?document=${adminDocument.id}`); await router.isReady();
  const wrapper = mount(PermissionsView, { attachTo: document.body, global: { plugins: [router], provide: { [sessionKey]: session } } });
  await flushPromises(); return { wrapper, session };
}
it("grant_confirmation_is_specific_and_success_restores_focus", async () => {
  const revoke = vi.spyOn(accessApi, "revokeGrant").mockResolvedValue();
  const { wrapper } = await mountView();
  await wrapper.get('button[aria-label^="Отозвать ролевое"]').trigger("click");
  expect(revoke).not.toHaveBeenCalled();
  expect(wrapper.get('[aria-label="Подтверждение отзыва разрешения"]').text()).toContain(adminDocument.title);
  expect(wrapper.get('[aria-label="Подтверждение отзыва разрешения"]').text()).toContain(`(${accessRole.code})`);
  vi.spyOn(accessApi, "grants").mockResolvedValue([directGrant]);
  await wrapper.get('[data-action="confirm-revoke"]').trigger("click"); await flushPromises();
  expect(revoke).toHaveBeenCalledWith(adminDocument.id, roleGrant.id, expect.any(String), expect.any(AbortSignal));
  expect(wrapper.text()).toContain("Прямое");
  expect(document.activeElement).toBe(wrapper.get("#grants-heading").element); wrapper.unmount();
});
it("ordinary_identity_cannot_load_administrative_metadata", async () => {
  const { wrapper } = await mountView(false);
  expect(vi.spyOn(accessApi, "users")).not.toHaveBeenCalled(); expect(vi.spyOn(accessApi, "documents")).not.toHaveBeenCalled();
  expect(wrapper.find("select").exists()).toBe(false); wrapper.unmount();
});
