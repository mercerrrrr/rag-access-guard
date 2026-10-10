import { z } from "zod";

export const docxMediaType = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export const allowedDocumentSchema = z.object({
  id: z.uuid(), title: z.string(), active_version_id: z.uuid(),
}).readonly();
export const adminDocumentSchema = z.object({
  id: z.uuid(), title: z.string(), active_version_id: z.uuid().nullable(),
  is_active: z.boolean(), created_at: z.string(),
}).readonly();
export const documentVersionSchema = z.object({
  id: z.uuid(), document_id: z.uuid(),
  status: z.enum(["stored", "chunked", "indexing", "ready", "failed"]),
  created_at: z.string(), content_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  byte_size: z.number().int().nonnegative(),
}).readonly();
export const documentTextSchema = z.object({
  document_id: z.uuid(), document_version_id: z.uuid(), text: z.string(),
}).readonly();
export type AllowedDocument = z.infer<typeof allowedDocumentSchema>;
export type AdminDocument = z.infer<typeof adminDocumentSchema>;
export type DocumentVersion = z.infer<typeof documentVersionSchema>;
export type DocumentText = z.infer<typeof documentTextSchema>;
export type DocumentEntry = AllowedDocument | AdminDocument;
export type DocumentPatch = { readonly title?: string; readonly is_active?: boolean };
export type DocumentTarget = { readonly documentId: string; readonly versionId: string };
export type DocumentUpload = { readonly title: string; readonly file: File };
export type VersionUpload = { readonly documentId: string; readonly file: File };
export type DocumentUpdate = { readonly documentId: string; readonly patch: DocumentPatch };
export interface DocumentsApi {
  allowed(signal: AbortSignal): Promise<readonly AllowedDocument[]>;
  admin(signal: AbortSignal): Promise<readonly AdminDocument[]>;
  versions(documentId: string, signal: AbortSignal): Promise<readonly DocumentVersion[]>;
  text(target: DocumentTarget, signal: AbortSignal): Promise<DocumentText>;
  upload(upload: DocumentUpload, token: string, signal: AbortSignal): Promise<AdminDocument>;
  uploadVersion(upload: VersionUpload, token: string, signal: AbortSignal): Promise<DocumentVersion>;
  update(update: DocumentUpdate, token: string, signal: AbortSignal): Promise<AdminDocument>;
}
