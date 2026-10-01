import type { ChatApi, SourceView, ThreadDetail, MessageResponse } from "@/api/types";

export const source: SourceView = {
  document_id: "00000000-0000-4000-8000-000000000001",
  document_version_id: "00000000-0000-4000-8000-000000000002",
  chunk_id: "00000000-0000-4000-8000-000000000003", title: "Регламент",
  url: "/api/documents/00000000-0000-4000-8000-000000000001/versions/00000000-0000-4000-8000-000000000002/content?chunk_id=00000000-0000-4000-8000-000000000003",
};
export const thread: ThreadDetail = {
  id: "00000000-0000-4000-8000-000000000010", title: "Новый диалог", revision: 0,
  created_at: "2026-10-01T00:00:00Z", turns: [],
};
export const reply: MessageResponse = { thread_revision: 1, replayed: false, turn: {
  id: "00000000-0000-4000-8000-000000000020",
  request_id: "00000000-0000-4000-8000-000000000030", user_input: "Вопрос", state: "available",
  answer: "Защищённый ответ", sources: [source], message: null,
} };
export function chatApi(overrides: Partial<ChatApi> = {}): ChatApi {
  return {
    list: () => Promise.resolve([thread]), create: () => Promise.resolve(thread),
    detail: (id) => Promise.resolve({ ...thread, id }), send: () => Promise.resolve(reply),
    source: () => Promise.resolve({ ...source, text: "Защищённый текст" }), ...overrides,
  };
}
