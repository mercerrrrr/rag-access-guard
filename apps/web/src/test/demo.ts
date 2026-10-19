import type { AssistantGuidanceApi, DemoProfile, SuggestionList, SystemStatus } from "@/api/demo";

export const suggestions: SuggestionList = {
  items: [{ id: "first", question: "Какие правила доступны?" }], policy_revision: 2,
};
export const profile: DemoProfile = { enabled: false, title: "Помощник по базе знаний", subtitle: null,
  notice: "Исследовательский прототип", logo_url: null };
export const system: SystemStatus = { mode: "local", model: { name: "audited", state: "ready" },
  search: { state: "ready" } };
export function guidanceApi(overrides: Partial<AssistantGuidanceApi> = {}): AssistantGuidanceApi {
  return { suggestions: () => Promise.resolve(suggestions), profile: () => Promise.resolve(profile),
    status: () => Promise.resolve(system), ...overrides };
}
