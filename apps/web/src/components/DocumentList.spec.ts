import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import DocumentList from "@/components/DocumentList.vue";
import { document } from "@/test/documents";

it("selects_document_by_id_without_linkifying_untrusted_title", async () => {
  const wrapper = mount(DocumentList, { props: { documents: [{ ...document, title: "<script>test</script>" }], selectedId: null, busy: false } });
  await wrapper.get("button").trigger("click");
  expect(wrapper.emitted("select")).toEqual([[document.id]]);
  expect(wrapper.find("script").exists()).toBe(false);
  expect(wrapper.text()).toContain("<script>test</script>");
});

it("empty_registry_is_explicit", () => {
  const wrapper = mount(DocumentList, { props: { documents: [], selectedId: null, busy: false } });
  expect(wrapper.text()).toContain("Документов нет");
  expect(wrapper.find("button").exists()).toBe(false);
});

it("shows_authorized_origin_and_removes_it_with_the_document", async () => {
  const origin = { kind: "synthetic_demo" as const, publisher: "Учебный демонстрационный корпус",
    source_url: null, retrieved_at: null, published_on: null, source_sha256: "a".repeat(64),
    transformation_revision: "v1" };
  const wrapper = mount(DocumentList, { props: {
    documents: [{ ...document, origin }], selectedId: null, busy: false,
  } });
  expect(wrapper.text()).toContain("Вымышленный учебный пример, не документ МЭИ.");
  await wrapper.setProps({ documents: [] });
  expect(wrapper.find(".source-origin").exists()).toBe(false);
  expect(wrapper.text()).not.toContain(origin.publisher);
  wrapper.unmount();
});
