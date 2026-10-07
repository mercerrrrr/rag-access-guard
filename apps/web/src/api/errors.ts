export type ApiErrorCode = "query_too_long";

export class ApiError extends Error {
  override readonly name = "ApiError";
  readonly status: number;
  readonly retryAfter: number | null;
  readonly code: ApiErrorCode | null;

  constructor(status: number, retryAfter: number | null = null, code: ApiErrorCode | null = null) {
    super("Request failed");
    this.status = status;
    this.retryAfter = retryAfter;
    this.code = code;
  }
}

export function errorMessage(error: ApiError): string {
  if (error.status === 422 && error.code === "query_too_long") {
    return "Вопрос слишком длинный. Сократите его и попробуйте ещё раз.";
  }
  switch (error.status) {
    case 401: return "Сессия завершена. Войдите снова.";
    case 403: return "Действие недоступно. Обновите сессию и повторите действие вручную.";
    case 409: return "Сессия уже существует. Выйдите перед сменой аккаунта.";
    case 429: return error.retryAfter === null
      ? "Слишком много запросов. Повторите позже."
      : `Слишком много запросов. Повторите через ${String(error.retryAfter)} с.`;
    default: return "Сервер недоступен. Повторите действие позже.";
  }
}
