import { readonly, ref } from "vue";

import type { SessionApi, UserDto } from "@/api/types";
import { ApiError, errorMessage } from "@/api/errors";

export function createSessionState(api: SessionApi) {
  const status = ref<"loading" | "anonymous" | "authenticated" | "unavailable">("loading");
  const user = ref<UserDto | null>(null);
  const sessionEpoch = ref(0);
  const busy = ref(false);
  const message = ref("");
  const logoutPending = ref(false);
  const listeners = new Set<() => void>();
  let controller = new AbortController();
  let csrfToken = "";

  function clearProtectedState() {
    sessionEpoch.value += 1;
    controller.abort();
    controller = new AbortController();
    for (const listener of listeners) listener();
  }

  function onClear(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }

  function expire(reportExpiry = true) {
    user.value = null;
    csrfToken = "";
    status.value = "anonymous";
    clearProtectedState();
    message.value = reportExpiry ? errorMessage(new ApiError(401)) : "";
  }

  async function refresh() {
    if (busy.value || logoutPending.value) return;
    const previousUserId = user.value?.id;
    clearProtectedState();
    const epoch = sessionEpoch.value;
    status.value = "loading";
    user.value = null;
    message.value = "";
    try {
      const result = await api.me(controller.signal);
      if (epoch !== sessionEpoch.value) return;
      if (!csrfToken || previousUserId !== result.user.id) {
        const csrf = await api.csrf(controller.signal);
        if (epoch !== sessionEpoch.value) return;
        csrfToken = csrf.csrf_token;
      }
      user.value = result.user;
      status.value = "authenticated";
    } catch (error) {
      if (epoch !== sessionEpoch.value) return;
      if (!(error instanceof ApiError)) throw error;
      if (error.status === 401) { expire(previousUserId !== undefined); return; }
      status.value = "unavailable";
      message.value = errorMessage(error);
    }
  }

  async function signIn(login: string, password: string) {
    if (busy.value || logoutPending.value) return;
    if (user.value !== null) {
      message.value = errorMessage(new ApiError(409));
      return;
    }
    clearProtectedState();
    const epoch = sessionEpoch.value;
    busy.value = true;
    message.value = "";
    try {
      const challenge = await api.csrf(controller.signal);
      if (epoch !== sessionEpoch.value) return;
      const result = await api.login({ login, password }, challenge.csrf_token, controller.signal);
      if (epoch !== sessionEpoch.value) return;
      csrfToken = result.csrf_token;
      user.value = result.user;
      status.value = "authenticated";
    } catch (error) {
      if (epoch !== sessionEpoch.value) return;
      if (!(error instanceof ApiError)) throw error;
      csrfToken = "";
      status.value = error.status >= 500 || error.status === 0 ? "unavailable" : "anonymous";
      if (error.status === 409) {
        logoutPending.value = true;
        status.value = "unavailable";
      }
      message.value = error.status === 401
        ? "Не удалось войти. Проверьте логин и пароль." : errorMessage(error);
    } finally {
      if (epoch === sessionEpoch.value) busy.value = false;
    }
  }

  async function signOut() {
    if (busy.value) return;
    clearProtectedState();
    const epoch = sessionEpoch.value;
    const previousToken = csrfToken;
    csrfToken = "";
    user.value = null;
    status.value = "loading";
    busy.value = true;
    logoutPending.value = true;
    message.value = "";
    try {
      const token = previousToken || (await api.csrf(controller.signal)).csrf_token;
      if (epoch !== sessionEpoch.value) return;
      await api.logout(token, controller.signal);
      if (epoch !== sessionEpoch.value) return;
      status.value = "anonymous";
      logoutPending.value = false;
    } catch (error) {
      if (epoch !== sessionEpoch.value) return;
      if (!(error instanceof ApiError)) throw error;
      if (error.status === 401) {
        logoutPending.value = false;
        status.value = "anonymous";
        return;
      }
      status.value = "unavailable";
      message.value = "Выход на сервере не подтверждён. Повторите выход.";
    } finally {
      if (epoch === sessionEpoch.value) busy.value = false;
    }
  }

  async function runProtected<T>(operation: (signal: AbortSignal, token: string) => Promise<T>): Promise<T> {
    if (status.value !== "authenticated") throw new ApiError(401);
    const epoch = sessionEpoch.value;
    try {
      const value = await operation(controller.signal, csrfToken);
      if (epoch !== sessionEpoch.value) throw new ApiError(0);
      return value;
    } catch (error) {
      if (epoch !== sessionEpoch.value) throw new ApiError(0);
      if (!(error instanceof ApiError)) throw error;
      if (error.status === 401) expire();
      else {
        if (error.status === 403) csrfToken = "";
        clearProtectedState();
        message.value = errorMessage(error);
      }
      throw error;
    }
  }

  return {
    status: readonly(status), user: readonly(user), sessionEpoch: readonly(sessionEpoch),
    busy: readonly(busy), message: readonly(message), logoutPending: readonly(logoutPending),
    refresh, signIn, signOut, clearProtectedState, onClear, runProtected,
  };
}

export type SessionState = ReturnType<typeof createSessionState>;
