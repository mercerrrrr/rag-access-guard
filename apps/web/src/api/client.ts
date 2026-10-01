import ky from "ky";

import { ApiError } from "@/api/errors";

export type JsonRequest<T> = {
  readonly parse: (value: unknown) => T;
  readonly method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  readonly body?: unknown;
  readonly signal?: AbortSignal;
  readonly csrfToken?: string;
  readonly timeout?: 15000 | 150000;
};

export async function requestJson<T>(path: string, options: JsonRequest<T>): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (!path.startsWith("/api/") || url.origin !== window.location.origin
    || !url.pathname.startsWith("/api/") || url.hash) throw new ApiError(0);
  const method = options.method ?? "GET";
  const headers = new Headers({ Accept: "application/json" });
  if (method !== "GET") {
    if (!options.csrfToken) throw new ApiError(403);
    headers.set("X-CSRF-Token", options.csrfToken);
  }
  try {
    const response = await ky(url, {
      method, headers, credentials: "same-origin", cache: "no-store", redirect: "error",
      retry: 0, timeout: options.timeout ?? 15000, throwHttpErrors: false,
      ...(options.body instanceof FormData ? { body: options.body }
        : options.body === undefined ? {} : { json: options.body }),
      ...(options.signal === undefined ? {} : { signal: options.signal }),
    });
    if (!response.ok) {
      const raw = response.headers.get("Retry-After");
      const seconds = raw !== null && /^\d+$/.test(raw) ? Number(raw) : NaN;
      throw new ApiError(response.status,
        Number.isSafeInteger(seconds) && seconds <= 86400 ? seconds : null);
    }
    const body: unknown = response.status === 204 ? undefined : await response.json();
    return options.parse(body);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof Error) throw new ApiError(0);
    throw new ApiError(0);
  }
}
