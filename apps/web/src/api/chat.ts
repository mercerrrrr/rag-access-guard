import { z } from "zod";
import { ApiError } from "@/api/errors";
import { requestJson } from "@/api/client";
import { messageResponseSchema, sourceContentSchema, sourceSchema, threadDetailSchema,
  threadSchema, type ChatApi } from "@/api/types";

function threadPath(id: string): string {
  if (!z.uuid().safeParse(id).success) throw new ApiError(0);
  return `/api/chat/threads/${id}`;
}

export const chatApi: ChatApi = {
  list: (signal) => requestJson("/api/chat/threads", {
    signal, parse: (value) => z.array(threadSchema).readonly().parse(value),
  }),
  create: (csrfToken, signal) => requestJson("/api/chat/threads", {
    signal, csrfToken, method: "POST", body: {}, parse: (value) => threadSchema.parse(value),
  }),
  detail: (id, signal) => requestJson(threadPath(id), {
    signal, parse: (value) => {
      const thread = threadDetailSchema.parse(value);
      if (thread.id !== id) throw new ApiError(0);
      return thread;
    },
  }),
  send: (id, body, csrfToken, signal) => requestJson(`${threadPath(id)}/messages`, {
    signal, csrfToken, method: "POST", body, timeout: 150000, parse: (value) => {
      const response = messageResponseSchema.parse(value);
      if (response.turn.request_id !== body.request_id || response.turn.user_input !== body.user_input) {
        throw new ApiError(0);
      }
      return response;
    },
  }),
  source: (source, signal) => {
    const checked = sourceSchema.safeParse(source);
    if (!checked.success) return Promise.reject(new ApiError(0));
    return requestJson(checked.data.url, { signal, parse: (value) => sourceContentSchema.parse(value) });
  },
};
