import { inject, type InjectionKey } from "vue";

import type { SessionState } from "@/composables/useSession";

export const sessionKey: InjectionKey<SessionState> = Symbol("session");

export function useSession(): SessionState {
  const session = inject(sessionKey);
  if (session === undefined) throw new Error("Session provider is required");
  return session;
}
