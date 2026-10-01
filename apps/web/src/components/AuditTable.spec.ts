import { expect, it } from "vitest";
import { parseAuditEvent } from "@/api/audit";
import { auditEvent } from "@/test/audit";
import { mount } from "@vue/test-utils";
import AuditTable from "@/components/AuditTable.vue";

it("audit_never_renders_unapproved_payload", () => {
  const decoded = parseAuditEvent({ ...auditEvent, prompt: "SYNTHETIC_PRIVATE_PROMPT", csrf_token: "SYNTHETIC_TOKEN" });
  expect(JSON.stringify(decoded)).not.toContain("SYNTHETIC_");
  expect(decoded).toMatchObject({ event_type: "grant_removed", policy_revision: 3, principal_id: null });
});
it("renders_only_allowlisted_metadata_and_explicit_utc_time", () => {
  const extra = { ...auditEvent, prompt: "PRIVATE", token: "SECRET" };
  const wrapper = mount(AuditTable, { props: { items: [extra] } });
  expect(wrapper.text()).toContain(auditEvent.id); expect(wrapper.text()).toContain(auditEvent.document_id);
  expect(wrapper.text()).toContain("UTC"); expect(wrapper.text()).toContain("Разрешение отозвано");
  expect(wrapper.text()).toContain("Не указан"); expect(wrapper.text()).not.toContain("PRIVATE");
  expect(wrapper.text()).not.toContain("SECRET"); expect(wrapper.findAll("tbody tr")).toHaveLength(1);
});
