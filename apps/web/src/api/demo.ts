import { z } from "zod";
import { requestJson } from "@/api/client";

const suggestionSchema = z.strictObject({
  id: z.string().min(1).max(100).regex(/^[A-Za-z0-9_.-]+$/),
  question: z.string().min(1).refine(value => value.trim().length > 0 && Array.from(value).length <= 4096),
}).readonly();
const suggestionListSchema = z.strictObject({
  items: z.array(suggestionSchema).max(4).readonly(),
  policy_revision: z.number().int().nonnegative(),
}).readonly();
const demoProfileSchema = z.strictObject({
  enabled: z.literal(false), title: z.literal("Помощник по базе знаний"), subtitle: z.null(),
  notice: z.literal("Исследовательский прототип"), logo_url: z.null(),
}).readonly();
const systemStatusSchema = z.strictObject({
  mode: z.enum(["disabled", "test", "local"]),
  model: z.strictObject({ name: z.string().nullable(),
    state: z.enum(["ready", "unavailable", "disabled", "test"]) }).readonly(),
  search: z.strictObject({ state: z.enum(["ready", "unavailable"]) }).readonly(),
}).readonly();

export type Suggestion = z.infer<typeof suggestionSchema>;
export type SuggestionList = z.infer<typeof suggestionListSchema>;
export type DemoProfile = z.infer<typeof demoProfileSchema>;
export type SystemStatus = z.infer<typeof systemStatusSchema>;
export interface AssistantGuidanceApi {
  suggestions(signal: AbortSignal): Promise<SuggestionList>;
  profile(signal: AbortSignal): Promise<DemoProfile>;
  status(signal: AbortSignal): Promise<SystemStatus>;
}
export const demoApi: AssistantGuidanceApi = {
  suggestions: signal => requestJson("/api/chat/suggestions", { signal, parse: value => suggestionListSchema.parse(value) }),
  profile: signal => requestJson("/api/demo/profile", { signal, parse: value => demoProfileSchema.parse(value) }),
  status: signal => requestJson("/api/system/status", { signal, parse: value => systemStatusSchema.parse(value) }),
};
