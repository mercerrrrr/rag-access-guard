import { z } from "zod";
import type { AdminDocument } from "@/api/documentTypes";

export const userSummarySchema = z.object({ id: z.uuid(), login: z.string(), display_name: z.string(),
  is_active: z.boolean(), is_admin: z.boolean() }).readonly();
export const roleSchema = z.object({ id: z.uuid(), code: z.string(), display_name: z.string() }).readonly();
export const grantTargetSchema = z.union([
  z.object({ user_id: z.uuid() }).strict(), z.object({ role_id: z.uuid() }).strict(),
]);
export const grantSchema = z.object({ id: z.uuid(), document_id: z.uuid(), user_id: z.uuid().nullable(),
  role_id: z.uuid().nullable() }).refine(value => (value.user_id === null) !== (value.role_id === null)).readonly();
export type UserSummary = z.infer<typeof userSummarySchema>;
export type Role = z.infer<typeof roleSchema>;
export type Grant = z.infer<typeof grantSchema>;
export type GrantTarget = z.infer<typeof grantTargetSchema>;
export type RoleInput = { readonly code: string; readonly display_name: string };
export type UserPatch = { readonly is_active?: boolean; readonly is_admin?: boolean };
export interface AccessApi {
  users(signal: AbortSignal): Promise<readonly UserSummary[]>;
  roles(signal: AbortSignal): Promise<readonly Role[]>;
  documents(signal: AbortSignal): Promise<readonly AdminDocument[]>;
  grants(documentId: string, signal: AbortSignal): Promise<readonly Grant[]>;
  members(roleId: string, signal: AbortSignal): Promise<readonly UserSummary[]>;
  createGrant(documentId: string, target: GrantTarget, token: string, signal: AbortSignal): Promise<Grant>;
  revokeGrant(documentId: string, grantId: string, token: string, signal: AbortSignal): Promise<void>;
  addMember(roleId: string, userId: string, token: string, signal: AbortSignal): Promise<void>;
  removeMember(roleId: string, userId: string, token: string, signal: AbortSignal): Promise<void>;
  updateUser(userId: string, patch: UserPatch, token: string, signal: AbortSignal): Promise<UserSummary>;
  createRole(value: RoleInput, token: string, signal: AbortSignal): Promise<Role>;
  renameRole(roleId: string, displayName: string, token: string, signal: AbortSignal): Promise<Role>;
}
