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
Реализован серверный вход по паролю Argon2id, восстановление сессии, CSRF
и выход с атомарным отзывом. Выдача разрешений и проверка доступа к документам
ещё не реализованы; признак администратора не даёт доступ к документам.

Независимый Python-пакет `rag_access_guard` предоставляет `Guard.authorize_read`:
он проверяет полный набор ссылок на источники по одной согласованной политике,
отказывает при неизвестном происхождении, противоречивом решении или сбое адаптера.
Пакет не зависит от API, базы данных или модели. Аутентификация пользователя,
расчёт разрешений и транзакционная выдача остаются обязанностью приложения.
Адаптер БД и генерация ответов пока не подключены.

Сборка отдельного пакета: `uv build --package rag-access-guard`.

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
$env:RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET = uv run python -c "import secrets; print(secrets.token_hex(32))"
$env:RAG_ACCESS_GUARD_AUTH_ORIGIN = "http://localhost:5173"
$env:RAG_ACCESS_GUARD_LOOPBACK_DEVELOPMENT = "true"
uv run --env-file .env alembic -c apps/api/alembic.ini upgrade head
uv run --env-file .env rag-access-guard-api
```

После запуска:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health/live
Invoke-RestMethod http://127.0.0.1:8000/api/health/ready
```

`RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET` — обязательный приватный секрет оператора:
32 случайных байта в виде 64 hex-символов. Команда выше создаёт его для текущего
локального запуска; для нескольких процессов используется одно сохранённое
значение из приватной конфигурации. Не публикуйте его и не добавляйте в Git.
Тесты используют отдельный случайный synthetic secret.

По умолчанию auth требует HTTPS и использует cookies `__Host-rag_session`,
`__Host-rag_preauth`, `__Host-rag_csrf`: `Secure; SameSite=Lax; Path=/`, без
Domain; первые две — HttpOnly. Явный loopback development mode разрешает только
HTTP origin на localhost или числовом loopback и loopback bind host. Он использует
отдельные cookies `rag_session_local`, `rag_preauth_local`, `rag_csrf_local` без
Secure. Origin задаётся точно, включая порт; wildcard и `null` не разрешены.
Встроенный сервер не доверяет proxy headers и слушает `127.0.0.1` по умолчанию
(`RAG_ACCESS_GUARD_BIND_HOST`). Для браузерного API используйте same-origin proxy;
чужие credentialed CORS origins не разрешены. Веб-форма входа пока не добавлена.

## Пользователи и сессии

Первого пользователя создаёт доверенный локальный оператор:

```powershell
uv run --env-file .env rag-access-guard-create-user --login reader --display-name "Reader"
uv run --env-file .env rag-access-guard-create-user --help
```

Пароль и подтверждение вводятся через скрытый prompt; флагов или переменных
окружения для пароля нет. Минимум 8 символов, максимум 1024 UTF-8 байта;
пробелы и регистр пароля сохраняются. `--admin` даёт административный признак,
но не document grants. Повторный login не перезаписывает пользователя.

Протокол: `GET /api/auth/csrf` → `POST /api/auth/login` с JSON `{login,password}`,
точным `Origin` и `X-CSRF-Token` → `GET /api/auth/me`. Bootstrap устанавливает
одноразовый pre-auth challenge на 10 минут; после login выдаются новая сессия
и новый CSRF. При reload `/me` → `/csrf` восстанавливают личность и сохраняют
здоровый CSRF между вкладками. `POST /api/auth/logout` отправляется без тела
с Origin и CSRF; он отзывает сессию и удаляет cookies. Все auth ответы — no-store.

Сессия действует максимум 8 часов и истекает после 30 минут бездействия;
каждый gate повторно проверяет пользователя и серверное время. В БД хранятся
только SHA-256 digests высокоэнтропийных токенов. Login ограничен 20 попытками
на socket IP и 5 на пару IP/нормализованный login за 10 минут; bootstrap —
20 на IP и 1000 неистёкших challenges глобально. Отказ — одинаковый 429 с
Retry-After. Это локальная DB-защита прототипа; доверие reverse proxy и внешнее
развёртывание требуют отдельной настройки.

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
