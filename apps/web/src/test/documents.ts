import type { AdminDocument, DocumentVersion, DocumentsApi } from "@/api/documentTypes";

export const document: AdminDocument = {
  id: "00000000-0000-4000-8000-000000000054", title: "Регламент",
  active_version_id: "00000000-0000-4000-8000-000000000055",
  is_active: true, created_at: "2026-10-01T00:00:00Z",
};
export const version: DocumentVersion = {
  id: "00000000-0000-4000-8000-000000000055", document_id: document.id,
  status: "ready", created_at: document.created_at, content_sha256: "0".repeat(64), byte_size: 24,
};
export function documentsApi(overrides: Partial<DocumentsApi> = {}): DocumentsApi {
  return {
    allowed: () => Promise.resolve([{ id: document.id, title: document.title, active_version_id: version.id }]),
    admin: () => Promise.resolve([document]), versions: () => Promise.resolve([version]),
    text: () => Promise.resolve({ document_id: document.id, document_version_id: version.id, text: "PRIVATE_54" }),
    upload: () => Promise.resolve(document), uploadVersion: () => Promise.resolve(version),
    update: () => Promise.resolve(document), ...overrides,
  };
}
