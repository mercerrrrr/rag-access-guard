import { expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import type { DocumentsApi } from "@/api/documentTypes";
import { createDocumentsState } from "@/composables/useDocuments";
import { createSessionState } from "@/composables/useSession";
import { document, documentsApi, version } from "@/test/documents";
import { deferred, sessionApi, student } from "@/test/session";

async function setup(overrides: Partial<DocumentsApi>) {
  const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: true } }) }));
  await session.refresh();
  const state = createDocumentsState(documentsApi(overrides), session);
  await state.refresh();
  await state.select(document.id);
  return state;
}

it("new_version_becomes_active_only_after_server_confirms", async () => {
  const nextVersion = { ...version, id: "00000000-0000-4000-8000-000000000056" };
  const barrier = deferred<undefined>();
  let ready = false;
  const state = await setup({
    admin: () => Promise.resolve([{ ...document, active_version_id: ready ? nextVersion.id : version.id }]),
    versions: () => Promise.resolve(ready ? [version, nextVersion] : [version]),
    uploadVersion: async () => { await barrier.promise; ready = true; return nextVersion; },
  });
  await state.read();
  const pending = state.uploadVersion({ documentId: document.id, file: new File(["next"], "next.txt") });
  expect(state.content.value).toBeNull();
  expect(state.versions.value.some((item) => item.id === nextVersion.id)).toBe(false);
  barrier.resolve(undefined);
  await pending;
  expect(state.selected.value?.active_version_id).toBe(nextVersion.id);
  expect(state.versions.value).toEqual([version, nextVersion]);
  state.dispose();
});

it("failed_version_refetches_truthful_status_and_preserves_old_active_version", async () => {
  let failed = false;
  const broken = { ...version, id: "00000000-0000-4000-8000-000000000056", status: "failed" as const };
  const state = await setup({ versions: () => Promise.resolve(failed ? [version, broken] : [version]),
    uploadVersion: () => { failed = true; return Promise.reject(new ApiError(503)); } });
  await state.uploadVersion({ documentId: document.id, file: new File(["next"], "next.txt") });
  expect(state.versions.value).toEqual([version, broken]);
  expect(state.selected.value?.active_version_id).toBe(version.id);
  expect(state.message.value).not.toBe("");
  state.dispose();
});

it("rename_and_deactivation_use_confirmed_registry", async () => {
  let current = document;
  const state = await setup({ admin: () => Promise.resolve([current]),
    update: ({ patch }) => { current = { ...current, ...patch }; return Promise.resolve(current); } });
  await state.update({ documentId: document.id, patch: { title: "Новое имя", is_active: false } });
  expect(state.items.value).toEqual([{ ...document, title: "Новое имя", is_active: false }]);
  state.dispose();
});
