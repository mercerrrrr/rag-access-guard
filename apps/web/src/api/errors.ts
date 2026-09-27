export class ApiError extends Error {
  override readonly name = "ApiError";
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(status: number, retryAfter: number | null = null) {
    super("Request failed");
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export function errorMessage(error: ApiError): string {
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
