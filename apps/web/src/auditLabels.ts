import type { AuditEventView } from "@/api/audit";
export const eventLabels: Record<AuditEventView["event_type"], string> = {
  session_created: "Сессия открыта", session_revoked: "Сессия отозвана", login_denied: "Вход отклонён",
  user_changed: "Пользователь изменён", role_changed: "Роль изменена", membership_added: "Участник добавлен",
  membership_removed: "Участник исключён", document_changed: "Документ изменён", grant_added: "Разрешение выдано",
  grant_removed: "Разрешение отозвано", access_checked: "Доступ проверен",
};
export const stageLabels: Record<AuditEventView["stage"], string> = {
  authentication: "Аутентификация", policy: "Политика", retrieval: "Поиск", context: "Контекст", release: "Выдача", read: "Чтение",
};
export const outcomeLabels: Record<AuditEventView["outcome"], string> = {
  allowed: "Разрешено", denied: "Отказано", success: "Выполнено", failure: "Ошибка",
};
