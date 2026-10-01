import { expect, it } from "vitest";
import { ApiError } from "@/api/errors";
import type { AllowedDocument, DocumentsApi, DocumentText } from "@/api/documentTypes";
import { createDocumentsState } from "@/composables/useDocuments";
import { createSessionState } from "@/composables/useSession";
import { document, documentsApi, version } from "@/test/documents";
import { deferred, sessionApi, student } from "@/test/session";

async function setup(overrides: Partial<DocumentsApi> = {}, admin = false) {
  const session = createSessionState(sessionApi({ me: () => Promise.resolve({ user: { ...student, is_admin: admin } }) }));
  await session.refresh();
  return { session, state: createDocumentsState(documentsApi(overrides), session) };
}

it("admin_without_grant_cannot_open_content", async () => {
  let reads = 0;
  const { state } = await setup({ allowed: () => Promise.resolve([]), text: () => {
    reads += 1; return Promise.reject(new ApiError(404));
  } }, true);
  await state.refresh();
  await state.select(document.id);
  await state.read();
  expect(state.items.value).toEqual([document]);
  expect(state.versions.value).toEqual([version]);
  expect(state.canReadContent.value).toBe(false);
  expect(reads).toBe(0);
  state.dispose();
});

it("reads_only_selected_allowed_active_version", async () => {
  const { state } = await setup();
  await state.refresh();
  await state.select(document.id);
  await state.read();
  expect(state.content.value?.text).toBe("PRIVATE_54");
  state.dispose();
});

it("stale_version_selection_clears_body", async () => {
  const late = deferred<DocumentText>();
  let calls = 0;
  const { state } = await setup({ text: () => {
    calls += 1;
    return calls === 1 ? Promise.resolve({ document_id: document.id, document_version_id: version.id, text: "PRIVATE_54" }) : late.promise;
  } });
  await state.refresh();
  await state.select(document.id);
  await state.read();
  expect(state.content.value?.text).toBe("PRIVATE_54");
  const pending = state.read();
  await state.select("00000000-0000-4000-8000-000000000056");
  late.resolve({ document_id: document.id, document_version_id: version.id, text: "LATE_SECRET" });
  await pending;
  expect(state.content.value).toBeNull();
  state.dispose();
});

it("late_list_cannot_restore_previous_user_documents", async () => {
  const late = deferred<readonly AllowedDocument[]>();
  const { session, state } = await setup({ allowed: () => late.promise });
  const pending = state.refresh();
  session.clearProtectedState();
  late.resolve([{ id: document.id, title: document.title, active_version_id: version.id }]);
  await pending;
  expect(state.items.value).toEqual([]);
  expect(state.content.value).toBeNull();
  state.dispose();
});

it("refresh_clears_protected_body_before_response", async () => {
  const { state } = await setup();
  await state.refresh();
  await state.select(document.id);
  await state.read();
  expect(state.content.value?.text).toBe("PRIVATE_54");
  const pending = state.refresh();
  expect(state.content.value).toBeNull();
  expect(state.items.value).toEqual([]);
  await pending;
  state.dispose();
});

it("failed_upload_does_not_create_ready_row", async () => {
  const { state } = await setup({ admin: () => Promise.resolve([]),
    upload: () => Promise.reject(new ApiError(503)) }, true);
  await state.upload({ title: "New", file: new File(["test"], "test.txt") });
  expect(state.items.value).toEqual([]);
  expect(state.versions.value).toEqual([]);
  state.dispose();
});

it("successful_upload_refreshes_server_registry_and_versions", async () => {
  let created = false;
  const { state } = await setup({ admin: () => Promise.resolve(created ? [document] : []),
    upload: () => { created = true; return Promise.resolve(document); } }, true);
  await state.refresh();
  await state.upload({ title: "New", file: new File(["test"], "test.txt") });
  expect(state.items.value).toEqual([document]);
  expect(state.versions.value).toEqual([version]);
  expect(state.selected.value?.id).toBe(document.id);
  state.dispose();
});

it("session_loss_clears_open_document_text", async () => {
  const { session, state } = await setup();
  await state.refresh();
  await state.select(document.id);
  await state.read();
  expect(state.content.value?.text).toBe("PRIVATE_54");
  session.clearProtectedState();
  expect(state.content.value).toBeNull();
  expect(state.selected.value).toBeNull();
  state.dispose();
});
