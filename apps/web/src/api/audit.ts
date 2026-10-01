import { z } from "zod";
import { requestJson } from "@/api/client";
import { ApiError } from "@/api/errors";

export const auditEventTypes = ["session_created", "session_revoked", "login_denied", "user_changed",
  "role_changed", "membership_added", "membership_removed", "document_changed", "grant_added",
  "grant_removed", "access_checked"] as const;
export const auditStages = ["authentication", "policy", "retrieval", "context", "release", "read"] as const;
export const auditOutcomes = ["allowed", "denied", "success", "failure"] as const;
const auditEventSchema = z.object({
  id: z.uuid(), occurred_at: z.iso.datetime({ offset: true }),
  actor_user_id: z.uuid().nullable(), principal_id: z.uuid().nullable(), document_id: z.uuid().nullable(),
  role_id: z.uuid().nullable(), grant_id: z.uuid().nullable(), event_type: z.enum(auditEventTypes),
  stage: z.enum(auditStages), outcome: z.enum(auditOutcomes),
  policy_revision: z.number().int().nonnegative(), source_count: z.number().int().nonnegative(),
}).readonly();
export type AuditEventView = z.infer<typeof auditEventSchema>;
export const parseAuditEvent = (value: unknown): AuditEventView => auditEventSchema.parse(value);
export type AuditFilters = Readonly<z.infer<typeof auditFiltersSchema>>;
export type AuditPage = { readonly items: readonly AuditEventView[]; readonly next_cursor: string | null };
export type AuditApi = { list: (filters: AuditFilters, cursor: string | null, signal: AbortSignal) => Promise<AuditPage> };
export const auditFiltersSchema = z.object({
  event_type: z.enum(auditEventTypes).optional(), stage: z.enum(auditStages).optional(),
  outcome: z.enum(auditOutcomes).optional(), since: z.iso.datetime({ offset: true }).optional(),
  until: z.iso.datetime({ offset: true }).optional(), limit: z.number().int().min(1).max(100).optional(),
}).strict().refine(value => value.since === undefined || value.until === undefined
  || Date.parse(value.since) < Date.parse(value.until));
const auditPageSchema = z.object({ items: z.array(auditEventSchema).max(100).readonly(),
  next_cursor: z.string().min(1).max(256).nullable() }).readonly();
export const auditApi: AuditApi = { list: (filters, cursor, signal) => {
  const checked = auditFiltersSchema.safeParse(filters);
  if (!checked.success || (cursor !== null && !z.string().min(1).max(256).safeParse(cursor).success)) {
    return Promise.reject(new ApiError(422));
  }
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(checked.data)) if (value !== undefined) query.set(key, String(value));
  if (cursor !== null) query.set("cursor", cursor);
  return requestJson(`/api/admin/audit?${query.toString()}`, { signal, parse: value => auditPageSchema.parse(value) });
} };
