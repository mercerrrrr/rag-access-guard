import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import PermissionEditor from "@/components/PermissionEditor.vue";

it("grant_form_never_sends_both_targets", async () => {
  const wrapper = mount(PermissionEditor, { props: { documentId: "d1",
    users: [{ id: "u1", login: "student", display_name: "Студент" }],
    roles: [{ id: "r1", code: "teacher", display_name: "Преподаватель" }] } });
  await wrapper.get('select[name="user_id"]').setValue("u1");
  await wrapper.get('select[name="target_kind"]').setValue("role");
  await wrapper.get('select[name="role_id"]').setValue("r1");
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("submit")?.[0]).toEqual([{ role_id: "r1" }]);
});
it("grant_select_labels_exclude_option_text", () => {
  const wrapper = mount(PermissionEditor, { props: { documentId: "d1", users: [], roles: [] } });
  const select = wrapper.get<HTMLSelectElement>('select[name="target_kind"]');
  expect(Array.from(select.element.labels).map(label => label.textContent.trim())).toEqual(["Получатель"]);
});
it("duplicate_display_names_keep_unique_user_and_role_labels", async () => {
  const users = [{ id: "u1", login: "ivan_one", display_name: "Иван Иванов" }, { id: "u2", login: "ivan_two", display_name: "Иван Иванов" }];
  const roles = [{ id: "r1", code: "reader_one", display_name: "Читатели" }, { id: "r2", code: "reader_two", display_name: "Читатели" }];
  const wrapper = mount(PermissionEditor, { props: { documentId: "d1", users, roles } });
  expect(wrapper.get('option[value="u1"]').text()).toBe("Иван Иванов (ivan_one)");
  expect(wrapper.get('option[value="u2"]').text()).toBe("Иван Иванов (ivan_two)");
  await wrapper.get('select[name="target_kind"]').setValue("role");
  expect(wrapper.get('option[value="r1"]').text()).toBe("Читатели (reader_one)");
  expect(wrapper.get('option[value="r2"]').text()).toBe("Читатели (reader_two)");
});
