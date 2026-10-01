import { z } from "zod";
import { ApiError } from "@/api/errors";
import { requestJson } from "@/api/client";
import { documentsApi } from "@/api/documents";
import { grantSchema, grantTargetSchema, roleSchema, userSummarySchema, type AccessApi } from "@/api/accessTypes";

function uuid(value: string): string {
  if (!z.uuid().safeParse(value).success) throw new ApiError(0);
  return value;
}
const grantsPath = (id: string) => `/api/admin/documents/${uuid(id)}/grants`;
const rolePath = (id: string) => `/api/admin/roles/${uuid(id)}`;
const memberPath = (roleId: string, userId: string) => `${rolePath(roleId)}/members/${uuid(userId)}`;
function list<T>(path: string, schema: z.ZodType<T>, signal: AbortSignal) {
  return requestJson(path, { signal, parse: value => z.object({ items: z.array(schema).readonly() }).parse(value).items });
}
const empty = (value: unknown): void => { z.undefined().parse(value); };
export const accessApi: AccessApi = {
  users: signal => list("/api/admin/users", userSummarySchema, signal),
  roles: signal => list("/api/admin/roles", roleSchema, signal),
  documents: signal => documentsApi.admin(signal),
  grants: (documentId, signal) => requestJson(grantsPath(documentId), { signal, parse: value => {
    const rows = z.object({ items: z.array(grantSchema).readonly() }).parse(value).items;
    if (rows.some(row => row.document_id !== documentId)) throw new ApiError(0);
    return rows;
  } }),
  members: (roleId, signal) => list(`${rolePath(roleId)}/members`, userSummarySchema, signal),
  createGrant: (documentId, target, csrfToken, signal) => {
    const parsed = grantTargetSchema.safeParse(target);
    if (!parsed.success) return Promise.reject(new ApiError(0));
    return requestJson(grantsPath(documentId), { method: "POST", body: parsed.data, csrfToken, signal,
      parse: value => {
        const grant = grantSchema.parse(value);
        if (grant.document_id !== documentId || ("user_id" in parsed.data
          ? grant.user_id !== parsed.data.user_id : grant.role_id !== parsed.data.role_id)) throw new ApiError(0);
        return grant;
      } });
  },
  revokeGrant: (documentId, grantId, csrfToken, signal) => requestJson(`${grantsPath(documentId)}/${uuid(grantId)}`,
    { method: "DELETE", csrfToken, signal, parse: empty }),
  addMember: (roleId, userId, csrfToken, signal) => requestJson(memberPath(roleId, userId),
    { method: "PUT", csrfToken, signal, parse: empty }),
  removeMember: (roleId, userId, csrfToken, signal) => requestJson(memberPath(roleId, userId),
    { method: "DELETE", csrfToken, signal, parse: empty }),
  updateUser: (userId, patch, csrfToken, signal) => requestJson(`/api/admin/users/${uuid(userId)}`,
    { method: "PATCH", body: patch, csrfToken, signal, parse: value => {
      const user = userSummarySchema.parse(value);
      if (user.id !== userId) throw new ApiError(0);
      return user;
    } }),
  createRole: (body, csrfToken, signal) => requestJson("/api/admin/roles", { method: "POST", body, csrfToken,
    signal, parse: value => roleSchema.parse(value) }),
  renameRole: (roleId, displayName, csrfToken, signal) => requestJson(rolePath(roleId), { method: "PATCH",
    body: { display_name: displayName }, csrfToken, signal, parse: value => {
      const role = roleSchema.parse(value);
      if (role.id !== roleId) throw new ApiError(0);
      return role;
    } }),
};
