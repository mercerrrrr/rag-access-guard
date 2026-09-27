import type { SessionApi, UserDto } from "@/api/types";

export const student: UserDto = {
  id: "00000000-0000-4000-8000-000000000001",
  login: "student", display_name: "Студент", is_admin: false,
};

export function sessionApi(overrides: Partial<SessionApi> = {}): SessionApi {
  return {
    me: () => Promise.resolve({ user: student }),
    csrf: () => Promise.resolve({ csrf_token: "session-token" }),
    login: () => Promise.resolve({ user: student, csrf_token: "new-session-token" }),
    logout: () => Promise.resolve(),
    ...overrides,
  };
}

export function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error("Promise was not initialized"); };
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}
