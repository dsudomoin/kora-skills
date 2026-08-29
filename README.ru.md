# Kora Skills

Скиллы для AI coding agents для разработки на Kora Framework.

> English version: [README.md](README.md)

## Что Это?

Репозиторий содержит пакеты скиллов для **Kora Framework**. Пакетов два — по одному на линейку
фреймворка, они устанавливаются и версионируются независимо:

| Пакет | Фреймворк | Group | Когда использовать |
| --- | --- | --- | --- |
| [`kora-v2`](plugins/kora-v2) | Kora 2.x | `io.koraframework` | Новые сервисы и любой проект уже на 2.x |
| [`kora-v1`](plugins/kora-v1) | Kora 1.x | `ru.tinkoff.kora` | Существующие сервисы, оставшиеся на 1.x |

Их можно держать установленными одновременно. Вложенные скиллы неймспейсятся именем плагина
(`kora-v2:kora-http-server` против `kora-v1:kora-http-server`), поэтому конфликта нет.

**Какой нужен?** Посмотрите `build.gradle` проекта. `ru.tinkoff.kora` → `kora-v1`.
`io.koraframework` → `kora-v2`. Проект с нуля → `kora-v2`.

Kora 2.0 — не совместимое обновление 1.x: сменилась group, контракты стали синхронными на
виртуальных потоках, `Context` удалён, R2DBC и Vert.x выброшены, а отказоустойчивость перешла со
строковых имён на типизированные спецификации. Каждый пакет учит своей линейке нативно; ни один из
них не является инструментом миграции. Корпус миграции лежит в
[kora-examples `migration/2.0`](https://github.com/kora-projects/kora-examples/tree/migration/2.0/migration).

## Быстрый Старт

```bash
git clone <repository-url> kora-skills
cd kora-skills
./plugins/kora-v2/install.sh     # Kora 2.x
./plugins/kora-v1/install.sh     # Kora 1.x
```

Это рекомендуемый универсальный путь для агентов: клонировать репозиторий, запустить установщик
нужной линейки, перезапустить целевого coding agent.

## Установка Через UI Агентов

### Claude Code / OpenClaude

Репозиторий является Claude-compatible plugin marketplace. Marketplace manifest:

```text
.claude-plugin/marketplace.json
```

Если текущая версия Claude Code или OpenClaude поддерживает plugins:

1. Откройте Claude Code.
2. Выполните `/plugin`.
3. Добавьте этот репозиторий как plugin marketplace.
4. Установите плагин `kora-v2` (или `kora-v1`, или оба).
5. Перезапустите agent или reload plugins.

Если plugin UI недоступен, используйте shell-установщик:

```bash
./plugins/kora-v2/install.sh
```

### OpenAI Codex

Codex использует отдельный repo-local marketplace manifest:

```text
.agents/plugins/marketplace.json
```

Из корня репозитория добавьте marketplace и установите нужный пакет:

```bash
codex plugin marketplace add .
codex plugin add kora-v2@kora-skills
codex plugin add kora-v1@kora-skills   # только если вы также поддерживаете сервисы на 1.x
```

Если plugin-команды Codex CLI недоступны, используйте shell-установщик:

```bash
./plugins/kora-v2/install.sh
```

### Локальные Директории Скиллов

Установщик работает со следующими локальными директориями (`<pkg>` — это `kora-v2` или `kora-v1`):

| Агент | Директория |
| --- | --- |
| Claude Code | `~/.claude/skills/<pkg>` |
| OpenAI Codex | `~/.agents/skills/<pkg>` |
| Pi Coding Agent | `~/.pi/skills/<pkg>` |
| OMP | `~/.omp/skills/<pkg>` |
| Cursor | `~/.cursor/skills/<pkg>` |
| OpenClaude / OpenClaw | `~/.openclaude/skills/<pkg>` |

Ручной запасной вариант для любого SKILL.md-совместимого агента:

```bash
mkdir -p ~/.local/share/skills/kora-v2
cp -r plugins/kora-v2/SKILL.md plugins/kora-v2/references plugins/kora-v2/skills \
      ~/.local/share/skills/kora-v2/
```

После публикации пакета в реестре скиллов:

```bash
npx skills add kora-projects/kora-v2
```

После установки перезапустите AI coding agent и спросите:

```text
Помоги создать микросервис на Kora с HTTP-эндпоинтами.
Добавь JDBC-репозиторий в этот Kora-сервис.
Создай SOAP/WSDL-клиент на Kora.
Разбери эту ошибку DI graph в Kora.
```

## Структура Репозитория

```text
kora-skills/
  README.md
  README.ru.md
  .claude-plugin/
    marketplace.json          # перечисляет оба плагина
  .agents/
    plugins/
      marketplace.json        # Codex; перечисляет оба плагина
  scripts/
    version.py                # пер-плагинная синхронизация версий (см. scripts/README.md)
  plugins/
    kora-v2/                  # пакет Kora 2.x
      SKILL.md
      skill.json
      install.sh
      README.md
      README.ru.md
      .claude-plugin/plugin.json
      .codex-plugin/plugin.json
      references/
        kora-docs-map.md
      skills/
        kora-starter/
        kora-di-compile/
        ...
    kora-v1/                  # пакет Kora 1.x, та же структура
```

## Инструкции Для AI Агентов

Если вы AI coding agent, читающий этот README, и пользователь просит установить Kora-скиллы:

1. Сначала определите линейку фреймворка. `ru.tinkoff.kora` в build-файлах проекта → `kora-v1`.
   `io.koraframework` → `kora-v2`. Смотреть нечего (новый проект) → `kora-v2`. Если ситуация
   действительно неоднозначна — спросите, не угадывайте.
2. Предпочитайте `./plugins/<pkg>/install.sh` из корня репозитория.
3. Если выполнение shell заблокировано, скопируйте `plugins/<pkg>/SKILL.md`,
   `plugins/<pkg>/references/` и `plugins/<pkg>/skills/` в директорию нужного агента из таблицы выше.
4. Для установки плагина в Claude Code или OpenClaude используйте `/plugin`, если доступно, и
   добавьте репозиторий как marketplace из `.claude-plugin/marketplace.json`.
5. Для Codex используйте `.agents/plugins/marketplace.json`, затем `codex plugin marketplace add .`
   и `codex plugin add <pkg>@kora-skills`.
6. После установки попросите пользователя перезапустить или перезагрузить целевого агента.
7. Не переименовывайте установленный пакет и не ставьте один поверх другого. `kora-v1` и `kora-v2` —
   разные директории для разных версий фреймворка.

## Содержимое Пакета

Каждый пакет содержит **39 доменных скиллов и один Codex meta-skill**:

- Core: `kora-di-compile`, `kora-di-runtime`, `kora-config-hocon`, `kora-config-yaml`, `kora-json`
- Project setup: `kora-project-setup-java`, `kora-project-setup-kotlin`, `kora-project-dependencies`
- HTTP и OpenAPI: `kora-http-server`, `kora-http-server-auth`, `kora-http-client`, `kora-http-client-auth`, `kora-openapi-generator-server`, `kora-openapi-generator-client`, `kora-openapi-management`
- Данные: `kora-database-jdbc`, `kora-database-cassandra`, `kora-database-migration`
- Messaging: `kora-kafka-producer`, `kora-kafka-consumer`
- gRPC и SOAP: `kora-grpc-server`, `kora-grpc-client`, `kora-soap-client`
- Telemetry: `kora-telemetry-tracing`, `kora-telemetry-metrics`, `kora-telemetry-logging`
- AOP: `kora-aop-caching`, `kora-aop-resilient`, `kora-aop-logging`, `kora-aop-scheduling-jdk`, `kora-aop-scheduling-quartz`, `kora-aop-validation`
- Тестирование: `kora-testing-junit-java`, `kora-testing-junit-kotlin`, `kora-testing-blackbox`
- Инструменты и обучение: `kora-s3`, `kora-mapstruct`, `kora-journal`, `kora-teacher`
- Совместимость с агентами: `kora-starter`

У пакетов совпадает *структура* скиллов, но не содержимое — каждый скилл написан под свою линейку
фреймворка.

## Сопровождение Версий

У каждого плагина своя версия. Не правьте её руками, используйте помощник:

```bash
python scripts/version.py            # напечатать версии всех плагинов
python scripts/version.py check      # гейт для CI / pre-commit
python scripts/version.py bump patch kora-v2
```

`set` и `bump` требуют явного имени плагина, чтобы никогда не поднять версию не той линейки.
Подробности: [`scripts/README.md`](scripts/README.md).

## Поддерживаемые Агенты

Пакеты подготовлены для агентов и рантаймов, понимающих скиллы в формате `SKILL.md` или локальные
директории скиллов:

- Claude Code
- OpenAI Codex
- Pi Coding Agent
- OMP
- Cursor
- Gemini CLI
- OpenClaude / OpenClaw
- Другие SKILL.md-совместимые агенты

## Документация

| Ресурс | Линейка | Ссылка |
| --- | --- | --- |
| Исходники фреймворка (релиз 2.x) | 2.x | https://github.com/kora-projects/kora/tree/2.0.0.RC1 |
| Мигрированные примеры приложений | 2.x | https://github.com/kora-projects/kora-examples/tree/migration/2.0 |
| Корпус миграции 1.x → 2.0 | 2.x | https://github.com/kora-projects/kora-examples/tree/migration/2.0/migration |
| Документация Kora Framework | 1.x | https://kora-projects.github.io/kora-docs |
| Официальные примеры | 1.x | https://github.com/kora-projects/kora-examples |
| Java template | 1.x | https://github.com/kora-projects/kora-java-template |
| Kotlin template | 1.x | https://github.com/kora-projects/kora-kotlin-template |
| Спецификация SKILL.md | — | https://agentskills.io/specification |

Сайта документации Kora 2.0 пока не существует. `kora-docs` и оба репозитория `kora-*-template`
по-прежнему описывают 1.x, поэтому отнесены к этой линейке и не должны использоваться как источник
истины по API 2.x.
