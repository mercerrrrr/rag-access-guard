import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import type { DocumentOrigin } from "@/api/origin";
import SourceOrigin from "./SourceOrigin.vue";

const base: DocumentOrigin = { kind: "user_upload", publisher: "Издатель", source_url: null,
  retrieved_at: null, published_on: null, source_sha256: "a".repeat(64), transformation_revision: "v1" };

it("labels_old_public_material_without_claiming_current_validity", () => {
  const wrapper = mount(SourceOrigin, { props: { origin: { ...base, kind: "official_public",
    published_on: "2015-01-02", retrieved_at: "2026-10-21T00:00:00Z", source_url: "https://example.org/doc" } } });
  expect(wrapper.text()).toContain("Открытый материал");
  expect(wrapper.text()).toContain("02.01.2015");
  expect(wrapper.text()).toContain("21.10.2026");
  expect(wrapper.text()).toContain("https://example.org/doc");
  expect(wrapper.text()).not.toContain("действующий");
  expect(wrapper.find("a").exists()).toBe(false);
  wrapper.unmount();
});

it("keeps_synthetic_warning_visible", () => {
  const wrapper = mount(SourceOrigin, { props: { origin: { ...base, kind: "synthetic_demo",
    publisher: "Учебный демонстрационный корпус" } } });
  expect(wrapper.text()).toContain("Вымышленный учебный пример, не документ МЭИ.");
  expect(wrapper.find("details").exists()).toBe(false);
  wrapper.unmount();
});

it("does_not_invent_publisher_or_dates_for_uploads", () => {
  const wrapper = mount(SourceOrigin, { props: { origin: { ...base, publisher: "я".repeat(200) } } });
  expect(wrapper.text()).toContain("Загруженный документ");
  expect(wrapper.text()).toContain("я".repeat(200));
  expect(wrapper.text()).not.toContain("МЭИ");
  expect(wrapper.text()).not.toContain("Опубликовано");
  expect(wrapper.text()).not.toContain("Invalid Date");
  wrapper.unmount();
});

it("distinguishes_unknown_publication_origin_from_access_denial", () => {
  const wrapper = mount(SourceOrigin, { props: { origin: null } });
  expect(wrapper.text()).toBe("Происхождение публикации не указано");
  wrapper.unmount();
});
