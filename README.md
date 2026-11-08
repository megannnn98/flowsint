# Flowsint

[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](./LICENSE)
[![Ethical Software](https://img.shields.io/badge/ethical-use-blue.svg)](./ETHICS.md)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20coffee-support-FFDD00?logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/dextmorgn)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-support-F16061?logo=ko-fi&logoColor=white)](https://ko-fi.com/P5P01W3GPJ)
[![Discord](https://img.shields.io/badge/Discord-Join%20Server-5865F2?logo=discord&logoColor=white)](https://discord.gg/aST9HMQr)


Flowsint — это open-source инструмент для исследования графов в OSINT, созданный для этичных расследований, прозрачности и проверки фактов.

> **Русская локализация.** Это форк [reconurge/flowsint](https://github.com/reconurge/flowsint): интерфейс и документация переведены на русский язык. Исходный (английский) текст остаётся в истории git и в оригинальном репозитории.

**Этика:** пожалуйста, прочитайте [ETHICS.md](./ETHICS.md) ([справочный перевод на русский](./ETHICS.ru.md)) — там изложены правила ответственного использования.

<img width="1439" height="899" alt="hero-dark" src="https://github.com/user-attachments/assets/01eb128e-bef4-486e-9276-c4da58f829ae" />


https://github.com/user-attachments/assets/eaabfa81-d7b3-414d-8cf7-f69b4e37bab6


https://github.com/user-attachments/assets/7457d94a-cf1d-4a97-949f-f9b1d8d92644


https://github.com/user-attachments/assets/65c3f26e-7132-4853-be45-21b8933688bd


## Участие в разработке

Flowsint пока находится на ранней стадии разработки и очень нуждается в помощи сообщества! Создавайте issue, предлагайте новые возможности и так далее.

## Быстрый старт

Не хотите читать? Понятно. Вот инструкции по установке:

### Linux / macOS

#### 1. Установите необходимые программы

- Docker
- Make

#### 2. Выполните команду установки

```bash
git clone https://github.com/reconurge/flowsint.git
cd flowsint
make prod
```

> **Важно про язык интерфейса.** `make prod` скачивает готовые образы с GitHub Container Registry, а они собраны из оригинального репозитория и показывают **английский** интерфейс. Чтобы запустить русский интерфейс из этого форка, соберите приложение из исходников, например командой `make dev` (см. раздел «Настройка среды разработки»).

### Windows

Make не нужен. Работает и в **командной строке (cmd)**, и в **PowerShell**.

#### 1. Установите необходимые программы

- [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) (перед следующим шагом убедитесь, что он **запущен**)
- Git

#### 2. Клонируйте репозиторий и подготовьте файлы окружения

```bat
git clone https://github.com/reconurge/flowsint.git
cd flowsint

copy .env.example .env
copy .env.example flowsint-api\.env
copy .env.example flowsint-core\.env
copy .env.example flowsint-app\.env
```

#### 3. Запустите

```bat
docker compose -f docker-compose.prod.yml up -d
```

Команда скачивает готовые образы с GitHub Container Registry — локальная сборка не нужна.

### Первый вход

Затем откройте [http://localhost:5173/register](http://localhost:5173/register) и создайте аккаунт. Учётных записей и паролей по умолчанию нет.


> ✅ OSINT-расследования требуют высокого уровня приватности. Все данные хранятся на вашем компьютере.

### Развёртывание в сети (команда / сервер)

Та же схема работает на сервере без доработок: фронтенд отдаёт интерфейс **и** внутренне проксирует все вызовы API, поэтому клиентам не нужна дополнительная настройка.

```bash
git clone https://github.com/reconurge/flowsint.git
cd flowsint
cp .env.example .env
# Отредактируйте .env — см. раздел «Перед открытием доступа из сети» ниже
docker compose -f docker-compose.prod.yml up -d
```

После этого любой пользователь сети сможет открыть Flowsint по адресу `http://<ip-сервера>:5173`.

**Перед открытием доступа из сети смените секреты по умолчанию в `.env`:**

- `AUTH_SECRET` — подписывает токены аутентификации. Сгенерировать: `openssl rand -hex 32`
- `MASTER_VAULT_KEY_V1` — шифрует сохранённые API-ключи. Сгенерировать: `python3 -c "import os, base64; print('base64:' + base64.b64encode(os.urandom(32)).decode())"`
- `NEO4J_PASSWORD` — пароль базы данных Neo4j.

**Также добавьте имя хоста или IP вашего сервера в список разрешённых Host-заголовков** в `flowsint-app/nginx.conf` (найдите `map $http_host $is_flowsint_host`). По умолчанию разрешены только `localhost` / `127.0.0.1` / `[::1]`: это защищает однопользовательские установки от DNS rebinding, поэтому для развёртываний в локальной сети и в интернете нужно явно добавить своё имя хоста. Две закомментированные строки-шаблона в том же блоке `map` показывают нужный формат.

В сеть открыт только порт `5173`. PostgreSQL, Redis, Neo4j и API привязаны на сервере к `127.0.0.1` и доступны только через прокси фронтенда.

Чтобы зафиксировать конкретную версию вместо `latest`, задайте `FLOWSINT_VERSION` в `.env` (например, `FLOWSINT_VERSION=1.2.10`).

**HTTPS (рекомендуется за пределами доверенной локальной сети):** поставьте перед портом 5173 любой обратный прокси. Пример с [Caddy](https://caddyserver.com/):

```
flowsint.example.com {
    reverse_proxy 127.0.0.1:5173
}
```

Если перед приложением стоит обратный прокси, привяжите порт приложения к localhost в `docker-compose.prod.yml` (`"127.0.0.1:5173:8080"`), чтобы клиенты могли подключаться только через HTTPS.


## Что это такое?

Flowsint — инструмент расследований на основе графов, ориентированный на разведку и OSINT (Open Source Intelligence, разведка по открытым источникам). Он позволяет исследовать связи между сущностями через визуальный интерфейс графа и автоматические обогатители (enrichers).

### Доступные обогатители

**Обогатители доменов**
- Reverse DNS Resolution — найти домены, указывающие на IP
- DNS Resolution — определить IP-адреса домена
- Subdomain Discovery — перечислить поддомены
- WHOIS Lookup — получить регистрационные данные домена
- Domain to Website — преобразовать домен в сущность «сайт»
- Domain to Root Domain — извлечь корневой домен
- Domain to ASN — найти ASN, связанный с доменом
- Domain History — получить исторические данные о домене

**Обогатители IP**
- IP Information — получить геолокацию и сетевые сведения
- IP to ASN — найти ASN для IP-адреса

**Обогатители ASN**
- ASN to CIDRs — получить диапазоны IP для ASN

**Обогатители CIDR**
- CIDR to IPs — перечислить IP-адреса в диапазоне

**Обогатители социальных сетей**
- Maigret — поиск имени пользователя на разных платформах

**Обогатители организаций**
- Organization to ASN — найти ASN, принадлежащие организации
- Organization Information — получить сведения о компании
- Organization to Domains — найти домены, принадлежащие организации

**Обогатители криптовалют**
- Wallet to Transactions — получить историю транзакций
- Wallet to NFTs — найти NFT, принадлежащие кошельку

**Обогатители сайтов**
- Website Crawler — обойти сайт и построить карту его структуры
- Website to Links — извлечь все ссылки
- Website to Domain — извлечь домен из URL
- Website to Webtrackers — выявить скрипты отслеживания
- Website to Text — извлечь текстовое содержимое

**Обогатители email**
- Email to Gravatar — найти профиль Gravatar
- Email to Breaches — проверить по базам утечек данных
- Email to Domains — найти связанные домены

**Обогатители телефонов**
- Phone to Breaches — проверить номер телефона по утечкам

**Обогатители физических лиц**
- Individual to Organization — найти связи с организациями
- Individual to Domains — найти домены, связанные с человеком

**Интеграционные обогатители**
- N8n Connector — подключение к рабочим процессам N8n

## Структура проекта

Проект разбит на автономные модули:

### Основные модули

- **flowsint-core**: базовые утилиты, оркестратор, хранилище секретов (vault), задачи Celery и базовые классы
- **flowsint-types**: модели Pydantic и определения типов
- **flowsint-enrichers**: модули обогатителей, логика сканирования и инструменты
- **flowsint-api**: сервер FastAPI, только маршруты API и схемы
- **flowsint-app**: frontend-приложение

### Зависимости между модулями

```
flowsint-app (frontend)
    ↓
flowsint-api (API-сервер)
    ↓
flowsint-core (оркестратор, задачи, vault)
    ↓
flowsint-enrichers (обогатители и инструменты)
    ↓
flowsint-types (типы)
```

## Настройка среды разработки

### Требования

- Docker

### Запуск

**Linux / macOS** (нужен **Make**):

```bash
make dev
```

**Windows** (cmd или PowerShell, без Make — сначала создайте файлы `.env`, см. [Быстрый старт](#windows)):

```bat
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml logs -f
```

### Разработка

Приложение доступно по адресу [http://localhost:5173](http://localhost:5173).

## Описание модулей

### flowsint-core

Базовые утилиты и классы, используемые всеми остальными модулями:

- Подключения к базам данных (PostgreSQL, Neo4j)
- Аутентификация и авторизация
- Логирование и обработка событий
- Управление конфигурацией
- Базовые классы для обогатителей и инструментов
- Вспомогательные функции

### flowsint-types

Модели Pydantic для всех типов данных:

- Domain, IP, ASN, CIDR
- Individual, Organization, Email, Phone
- Website, профили в соцсетях, учётные данные
- Криптокошельки, транзакции, NFT
- И многое другое...

### flowsint-enrichers

Модули обогатителей, обрабатывающие данные:

- Обогатители доменов (поддомены, WHOIS, разрешение имён)
- Обогатители IP (геолокация, поиск ASN)
- Обогатители соцсетей (Maigret, Sherlock)
- Обогатители email (утечки, Gravatar)
- Крипто-обогатители (транзакции, NFT)
- И многое другое...

### flowsint-api

Сервер FastAPI, который предоставляет:

- REST API
- Аутентификацию и управление пользователями
- Интеграцию с графовой базой данных
- Потоковую передачу событий в реальном времени

### flowsint-app

Frontend-приложение.

- Современный и удобный интерфейс
- Рассчитано на производительность (без лагов даже на тысячах узлов)

## Порядок разработки

1. **Добавление новых типов**: добавляйте в модуль `flowsint-types`
2. **Добавление новых обогатителей**: добавляйте в модуль `flowsint-enrichers`
3. **Добавление новых эндпоинтов API**: добавляйте в модуль `flowsint-api`
4. **Добавление новых утилит**: добавляйте в модуль `flowsint-core`

## Тестирование

У каждого модуля свой (неполный) набор тестов:

```bash
# Тесты модуля core
cd flowsint-core
uv run pytest

# Тесты модуля types
cd ../flowsint-types
uv run pytest

# Тесты модуля enrichers
cd ../flowsint-enrichers
uv run pytest

# Тесты модуля API
cd ../flowsint-api
uv run pytest
```

## Участие в разработке

1. Соблюдайте модульную структуру
2. Используйте [uv](https://docs.astral.sh/uv/) для управления зависимостями
3. Пишите тесты для новой функциональности
4. При необходимости обновляйте документацию
5. Какие проверки запускать перед коммитом, см. в [CONTRIBUTING.md](./CONTRIBUTING.md)


---

## ⚖️ Правовые и этические аспекты использования

**Этика:** пожалуйста, прочитайте [ETHICS.md](./ETHICS.md) — там изложены правила ответственного использования.

Flowsint создан **исключительно для законных и этичных расследований и исследований**.

Он создан, чтобы помогать:
- Исследователям и аналитикам по кибербезопасности
- Журналистам и OSINT-исследователям
- Правоохранительным органам и командам по расследованию мошенничества
- Организациям, которые проводят внутренний анализ киберугроз или цифровых рисков

**Flowsint нельзя использовать для:**
- Несанкционированного проникновения, слежки или сбора данных
- Преследования, раскрытия личных данных (доксинга) или травли людей
- Политических манипуляций, дезинформации или нарушения законов о защите частной жизни

Любое злоупотребление этим программным обеспечением строго запрещено и противоречит этическим принципам, изложенным в [ETHICS.md](./ETHICS.md).

## ❤️ Поддержка

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20coffee-support-FFDD00?logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/dextmorgn)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-support-F16061?logo=ko-fi&logoColor=white)](https://ko-fi.com/P5P01W3GPJ)
