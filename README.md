# RAG Access Guard

Исследовательский прототип динамического разграничения доступа к корпоративным знаниям в RAG-системе.

Проект проверяет, что приложение повторно авторизует извлечённые документы и сохранённые ответы с известным происхождением перед передачей модели, выдачей пользователю и последующим чтением.

## Граница защиты

Механизм работает с данными, для которых система сохранила точное происхождение: документ, его неизменяемую версию и фрагмент. Произвольный текст, вручную введённый пользователем, не получает происхождение по смысловому сходству и не относится к этой гарантии.

## Статус

Реализованы базовый FastAPI-сервис и адаптивная оболочка веб-приложения.
Интерфейс содержит разделы `Чат`, `Документы`, `Доступ` и `Аудит`; предметные
операции в них появятся на следующих этапах.

Добавлена схема пользователей, сессий, ролей, документов, прямых и ролевых
разрешений, ревизии политики и аудита. Сессии хранят только хеши токенов;
аудит — идентификаторы и коды событий без содержимого документов.
Вход, выдача разрешений и проверка доступа ещё не реализованы.

Сервис предоставляет две проверки состояния:

```text
GET /api/health/live
GET /api/health/ready
```

`live` подтверждает работу процесса. `ready` возвращает успешный ответ только
для PostgreSQL 18 с pgvector 0.8.6 и актуальной ревизией Alembic.

Успешный ответ:

```json
{"status":"ok"}
```

## Локальный запуск API

Требуются `uv` и Docker Desktop в режиме Linux-контейнеров. Версия Python
3.13.15 закреплена в `.python-version`, зависимости проекта — в `uv.lock`.

Локальные значения из `.env.example` предназначены только для разработки.

```powershell
Copy-Item .env.example .env
uv sync --frozen
docker compose up --detach --wait postgres
uv run --env-file .env alembic -c apps/api/alembic.ini upgrade head
uv run --env-file .env rag-access-guard-api
```

После запуска:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health/live
Invoke-RestMethod http://127.0.0.1:8000/api/health/ready
```

## Локальный запуск интерфейса

Требуются Node.js 24.21.0 и npm 11.19.0. Их точные версии закреплены в
`.node-version` и `apps/web/package.json`.

```powershell
Set-Location apps/web
npm ci
npm run dev
```

## Проверки

GitHub Actions выполняет эти проверки из чистого checkout в независимых
заданиях для Python/API и интерфейса, включая HTTP-пробы серверной точки
входа и production preview собранного интерфейса.

```shell
uv lock --check
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run pytest

Set-Location apps/web
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```
