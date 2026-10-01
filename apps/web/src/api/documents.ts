import { z } from "zod";
import { ApiError } from "@/api/errors";
import { requestJson } from "@/api/client";
import { adminDocumentSchema, allowedDocumentSchema, documentTextSchema, documentVersionSchema,
  type DocumentsApi } from "@/api/documentTypes";

function documentPath(id: string): string {
  if (!z.uuid().safeParse(id).success) throw new ApiError(0);
  return `/api/admin/documents/${id}`;
}

export const documentsApi: DocumentsApi = {
  allowed: (signal) => requestJson("/api/documents", {
    signal, parse: (value) => z.object({ items: z.array(allowedDocumentSchema).readonly() }).parse(value).items,
  }),
  admin: (signal) => requestJson("/api/admin/documents", {
    signal, parse: (value) => z.object({ items: z.array(adminDocumentSchema).readonly() }).parse(value).items,
  }),
  versions: (id, signal) => requestJson(`${documentPath(id)}/versions`, {
    signal, parse: (value) => {
      const versions = z.object({ items: z.array(documentVersionSchema).readonly() }).parse(value).items;
      if (versions.some((version) => version.document_id !== id)) throw new ApiError(0);
      return versions;
    },
  }),
  text: ({ documentId, versionId }, signal) => {
    if (!z.uuid().safeParse(documentId).success || !z.uuid().safeParse(versionId).success) {
      return Promise.reject(new ApiError(0));
    }
    return requestJson(`/api/documents/${documentId}/versions/${versionId}/text`, {
      signal, parse: (value) => {
        const content = documentTextSchema.parse(value);
        if (content.document_id !== documentId || content.document_version_id !== versionId) throw new ApiError(0);
        return content;
      },
    });
  },
  upload: ({ title, file }, csrfToken, signal) => {
    const body = new FormData();
    body.set("title", title);
    body.set("file", file);
    return requestJson("/api/admin/documents", { method: "POST", body, csrfToken, signal,
      timeout: 150000, parse: (value) => adminDocumentSchema.parse(value) });
  },
  uploadVersion: ({ documentId, file }, csrfToken, signal) => {
    const body = new FormData();
    body.set("file", file);
    return requestJson(`${documentPath(documentId)}/versions`, { method: "POST", body, csrfToken,
      signal, timeout: 150000, parse: (value) => {
        const version = documentVersionSchema.parse(value);
        if (version.document_id !== documentId) throw new ApiError(0);
        return version;
      } });
  },
  update: ({ documentId, patch }, csrfToken, signal) => requestJson(documentPath(documentId), {
    method: "PATCH", body: patch, csrfToken, signal, parse: (value) => {
      const document = adminDocumentSchema.parse(value);
      if (document.id !== documentId) throw new ApiError(0);
      return document;
    },
  }),
};
