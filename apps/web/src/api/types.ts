import { z } from "zod";

export const sourceSchema = z.object({
  document_id: z.uuid(), document_version_id: z.uuid(), chunk_id: z.uuid(),
  title: z.string(), url: z.string(),
}).refine((source) => source.url ===
  `/api/documents/${source.document_id}/versions/${source.document_version_id}/content?chunk_id=${source.chunk_id}`,
{ message: "Source URL does not match its provenance" }).readonly();
export type SourceView = z.infer<typeof sourceSchema>;

export const sourceContentSchema = z.object({
  document_id: z.uuid(), document_version_id: z.uuid(), chunk_id: z.uuid(),
  title: z.string(), text: z.string(),
}).readonly();
export type SourceContent = z.infer<typeof sourceContentSchema>;

export const threadSchema = z.object({
  id: z.uuid(), title: z.string(), revision: z.number().int().nonnegative(), created_at: z.string(),
}).readonly();
const turnIdentity = { id: z.uuid(), request_id: z.uuid(), user_input: z.string() };
const emptyAnswer = { answer: z.null(), sources: z.array(sourceSchema).length(0).readonly() };
export const turnSchema = z.discriminatedUnion("state", [
  z.object({ ...turnIdentity, ...emptyAnswer, state: z.literal("pending"), message: z.null() }),
  z.object({ ...turnIdentity, state: z.literal("available"), answer: z.string(),
    sources: z.array(sourceSchema).min(1).readonly(), message: z.null() }),
  z.object({ ...turnIdentity, ...emptyAnswer, state: z.literal("neutral"), message: z.enum([
    "Нет доступных источников для ответа.", "Не удалось получить ответ. Повторите запрос.",
    "Права изменились во время ответа. Повторите запрос.", "Генерация прервана. Отправьте новый запрос.",
  ]) }),
  z.object({ ...turnIdentity, ...emptyAnswer, state: z.literal("unavailable"),
    message: z.literal("Ответ недоступен: права на один из источников изменились.") }),
]).readonly();
export const threadDetailSchema = threadSchema.unwrap().extend({
  turns: z.array(turnSchema).readonly(),
}).readonly();
export const messageResponseSchema = z.object({
  thread_revision: z.number().int().nonnegative(), turn: turnSchema, replayed: z.boolean(),
}).readonly();
export type ThreadView = z.infer<typeof threadSchema>;
export type ThreadDetail = z.infer<typeof threadDetailSchema>;
export type TurnView = z.infer<typeof turnSchema>;
export type MessageResponse = z.infer<typeof messageResponseSchema>;
export interface MessageRequest {
  readonly request_id: string;
  readonly expected_thread_revision: number;
  readonly user_input: string;
}
export interface ChatApi {
  list(signal: AbortSignal): Promise<readonly ThreadView[]>;
  create(token: string, signal: AbortSignal): Promise<ThreadView>;
  detail(id: string, signal: AbortSignal): Promise<ThreadDetail>;
  send(id: string, body: MessageRequest, token: string, signal: AbortSignal): Promise<MessageResponse>;
  source(source: SourceView, signal: AbortSignal): Promise<SourceContent>;
}

export const userSchema = z.object({
  id: z.uuid(), login: z.string(), display_name: z.string(), is_admin: z.boolean(),
}).readonly();
export const sessionSchema = z.object({ user: userSchema }).readonly();
export const csrfSchema = z.object({ csrf_token: z.string().min(1) }).readonly();
export const loginSchema = z.object({ user: userSchema, csrf_token: z.string().min(1) }).readonly();
export type UserDto = z.infer<typeof userSchema>;
export type SessionDto = z.infer<typeof sessionSchema>;
export type CsrfDto = z.infer<typeof csrfSchema>;
export type LoginDto = z.infer<typeof loginSchema>;

export interface SessionApi {
  me(signal: AbortSignal): Promise<SessionDto>;
  csrf(signal: AbortSignal): Promise<CsrfDto>;
  login(credentials: { readonly login: string; readonly password: string },
    csrfToken: string, signal: AbortSignal): Promise<LoginDto>;
  logout(csrfToken: string, signal: AbortSignal): Promise<void>;
}
