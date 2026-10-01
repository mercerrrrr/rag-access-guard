import { ApiError } from "@/api/errors";

export type ApiResult<T> = { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: ApiError };

export async function protectedResult<T>(operation: () => Promise<T>): Promise<ApiResult<T>> {
  try { return { ok: true, value: await operation() }; }
  catch (error) {
    if (!(error instanceof ApiError) || error.status === 401 || error.status === 403) throw error;
    return { ok: false, error };
  }
}
