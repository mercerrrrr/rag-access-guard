import { z } from "zod";

import { requestJson } from "@/api/client";
import { csrfSchema, loginSchema, sessionSchema, type SessionApi } from "@/api/types";

export const sessionApi: SessionApi = {
  me: (signal) => requestJson("/api/auth/me", { signal, parse: (value) => sessionSchema.parse(value) }),
  csrf: (signal) => requestJson("/api/auth/csrf", { signal, parse: (value) => csrfSchema.parse(value) }),
  login: (body, csrfToken, signal) => requestJson("/api/auth/login", {
    method: "POST", body, csrfToken, signal, parse: (value) => loginSchema.parse(value),
  }),
  logout: (csrfToken, signal) => requestJson("/api/auth/logout", {
    method: "POST", csrfToken, signal, parse: (value) => { z.undefined().parse(value); },
  }),
};
