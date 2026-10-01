import type { AccessApi, Grant, Role, UserSummary } from "@/api/accessTypes";
import { document as adminDocument } from "@/test/documents";
export const accessUser: UserSummary = { id: "00000000-0000-4000-8000-000000000055", login: "reader",
  display_name: "Читатель", is_active: true, is_admin: false };
export const accessRole: Role = { id: "00000000-0000-4000-8000-000000000056", code: "reader", display_name: "Читатели" };
export const directGrant: Grant = { id: "00000000-0000-4000-8000-000000000057", document_id: adminDocument.id,
  user_id: accessUser.id, role_id: null };
export const roleGrant: Grant = { ...directGrant, id: "00000000-0000-4000-8000-000000000058", user_id: null, role_id: accessRole.id };
export function accessApi(overrides: Partial<AccessApi> = {}): AccessApi {
  return { users: () => Promise.resolve([accessUser]), roles: () => Promise.resolve([accessRole]),
    documents: () => Promise.resolve([adminDocument]), grants: () => Promise.resolve([directGrant, roleGrant]),
    members: () => Promise.resolve([accessUser]), createGrant: () => Promise.resolve(directGrant),
    revokeGrant: () => Promise.resolve(), addMember: () => Promise.resolve(), removeMember: () => Promise.resolve(),
    updateUser: () => Promise.resolve(accessUser), createRole: () => Promise.resolve(accessRole),
    renameRole: () => Promise.resolve(accessRole), ...overrides };
}
