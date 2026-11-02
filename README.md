# Aithena Python test task

Python CLI для анализа лицензий и функций через LangChain и перевода небольших
copyleft-файлов на Rust. Исходное условие — в `task.md`, исходники без изменений — в `data/`.

## Запуск

Требуются Python 3.11+, uv, API-ключ и модель с поддержкой structured output
OpenAI Chat Completions. По умолчанию Rust проверяется в изолированном Docker-контейнере.

```bash
uv sync --frozen
docker build --target rust-validator -t aithena-rust-validator:local .
export OPENAI_API_KEY='your-key'
export LLM_MODEL='gpt-5.4-mini-2026-03-17'
uv run python -m solution --input data --output outputs
```

Модель можно передать через `--model`. `--timeout 60` задаёт таймаут API-запроса.
Файл `.env` загружается автоматически из текущего каталога; другой путь задаётся через
`--env-file /path/to/file`. Уже заданные переменные окружения имеют приоритет. Исходники отправляются
выбранному API; ключ не сохраняется в отчёте.

Если Docker недоступен, `--skip-rust-check` позволяет сохранить перевод без проверки;
в JSON он будет помечен `rust_validation: "skipped"`.

Код завершения: `0` — все файлы обработаны; `1` — ошибка или неизвестная лицензия;
`2` — ошибка аргументов/обязательных настроек. Для частичного сбоя отчёт сохраняется,
обработка остальных файлов продолжается. Сообщения ошибок находятся в `results.json`.

## Логика

1. Рекурсивно прочитать UTF-8 файлы в стабильном порядке.
2. LLM извлекает язык, правообладателей, SPDX-название лицензии, категорию и функции.
   Ответ проверяется Pydantic; файл передаётся как JSON-данные, отдельно от system prompt.
   Имена и параметры функций независимо сверяются с Python AST / JavaScript Tree-sitter.
   Несовпадение останавливает обработку файла. Для других языков проверка помечается unsupported.
   Известные названия лицензий нормализуются; конфликт категории с известным SPDX — ошибка.
3. Python выбирает действие:
   - permissive → сохранить список функций;
   - copyleft, больше двух определений → сохранить список функций;
   - copyleft, не больше двух определений → второй запрос к LLM для перевода на Rust;
   - неизвестная/неоднозначная/закрытая лицензия → `unsupported`.
4. Rust проверяется через `rustc --crate-type=lib --emit=metadata`, без запуска кода.
   Отдельный контейнер не получает ключ, переменные окружения или файлы хоста; сеть отключена,
   файловая система read-only, пользователь непривилегированный, лимиты памяти/CPU/PID/времени.
   `--rust-backend local` явно отключает эту изоляцию и требует локальный `rustc`.
   Успешный перевод сохраняется, затем атомарно обновляется отчёт после каждого файла.

Транспортные ошибки имеют до двух повторных попыток SDK; невалидный структурированный
ответ — одну дополнительную попытку. Ошибки компиляции попадают в отчёт без заявления
об успешном переводе. Автоматическое исправление Rust не реализовано.

## Результаты

`outputs/results.json` содержит `schema_version: 2`, UTC-время обновления и список `results`.
Для каждого файла: `path`, `status`, `analysis`, `action`, `rust_path`,
`rust_validation`, `error`, SHA-256 исходника и статус независимой проверки функций. `analysis` содержит `language`, `copyright_holders`,
`license_name`, `license_category`, `functions` (список `name`/`argument_count`).
Количество определений равно длине `functions`.

Rust сохраняется как `outputs/rust/<исходный относительный путь>.rs`:
например `rust/1.py.rs`. Исходное расширение предотвращает коллизию `foo.py` / `foo.js`.
Пути в отчёте относительны к входному каталогу, а `rust_path` — к выходному.
При повторном запуске отчёт и соответствующие успешные переводы заменяются;
старые переводы не удаляются, поэтому актуальные файлы следует брать только из отчёта.
Входной и выходной каталоги не должны пересекаться.

В `examples/` находится **иллюстративный результат с подменёнными ответами LLM**,
а не запись настоящего API-запуска. Rust в нём написан вручную для проверки примера.

| Файл | Лицензия | Правообладатель | Ожидание |
|---|---|---|---|
| `1.py` | GPL-3.0-or-later | Elon Musk | Rust: публичные `foo` и `bar`, без автоматических вызовов |
| `2.py` | GPL-3.0-or-later | Guido Musk | `foo(3), bar(1), foo1(1), bar1(1), bar1(1)` |
| `3.py` | MIT | Brendan Eich | JavaScript: `hello(1)` |

## Допущения и ограничения

- Язык определяется по содержимому, а не расширению; `3.py` на самом деле JavaScript.
- Считаются определения, включая повторы, вложенные функции и методы; вызовы и
  анонимные функции не считаются. Учитываются объявленные параметры, включая
  `self`/`cls`, значения по умолчанию и каждый вариадический параметр как один.
- Двойные или противоречивые лицензии требуют ручного решения (`unknown`).
- Для ветвления список функций извлекается в первом запросе даже для малых GPL-файлов.
- Rust генерируется как библиотека со стандартной библиотекой, с сохранением
  лицензионных комментариев. Компиляция не доказывает семантическую эквивалентность.
- Классификация и извлечение зависят от модели: схема проверяет форму, не истинность.
  Защита промптом от инструкций внутри кода не является абсолютной гарантией.
- Лимит файла — 1 MB; бинарные/не-UTF-8 файлы и симлинки не поддерживаются.
  Это небольшое тестовое решение, без пакетного параллелизма и кэширования.

## Тесты

```bash
uv run python -m pytest --cov=solution --cov-branch --cov-report=term-missing --cov-report=json:coverage.json -q
uv run python scripts/check_coverage.py coverage.json
uv run ruff check solution tests
```

По умолчанию сеть не используется: ответы LLM подменены. Проверяются ветки и граница
2/3, дубликаты, передача JavaScript, ошибки API/схемы/компиляции, частичный сбой и
коллизии имён. Проверка реального компилятора пропускается, если `rustc` не установлен.

Необязательная интеграционная проверка исходного архива (платные API-запросы):

```bash
RUN_LLM_TESTS=1 uv run pytest -m integration -q
```

Нужны `OPENAI_API_KEY`, `LLM_MODEL`, Docker и собранный образ `aithena-rust-validator:local`.
Для pytest переменные нужно экспортировать заранее (CLI загружает `.env` самостоятельно). Этот тест проверяет реальные извлечённые
данные и компиляцию сгенерированного перевода.

## Docker

```bash
docker build -t aithena-solution .
mkdir -p outputs
docker run --rm --user "$(id -u):$(id -g)" \
  -e OPENAI_API_KEY -e LLM_MODEL \
  -v "$PWD/outputs:/results" \
  aithena-solution --output /results --rust-backend local
```

Для другого входа добавьте `-v "$PWD/my-data:/input:ro"` и `--input /input`.
В этом Docker-примере компилятор запускается в том же контейнере, что и API-клиент.
Для недоверенного кода рекомендуемый вариант — CLI на хосте с отдельным sandbox-образом;
не передавайте Docker socket в контейнер приложения.

Зависимости зафиксированы в `uv.lock`; ключ не включается в образ.

Использованный API LangChain:
[ChatOpenAI.with_structured_output](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI/with_structured_output).

## Лимиты, наблюдаемость и проверенный запуск

- `--max-output-tokens 8192` ограничивает ответ каждого запроса, включая reasoning.
- `--max-calls 100` ограничивает вызовы LangChain (включая повтор после ошибки схемы).
  Внутренние транспортные повторы SDK в этот счётчик не входят: максимум два на вызов.
  Это не денежный лимит; входные токены также оплачиваются.
- `usage.json` содержит модель, операцию, путь, длительность, статус и полученную статистику
  токенов. Данные исходников, текст ответов и ключи туда не записываются.
  При обрыве соединения провайдер может не вернуть usage; такие расходы неизвестны.
- Ошибки провайдера сохраняются без тела ответа, ошибки схемы — без исходных данных.
- `.github/workflows/ci.yml` проверяет Python 3.11–3.13, стиль, тесты и Docker-сборки.
  Workflow опубликован в GitHub; см. раздел GitHub CI.
- `live-results/` (локальный каталог, исключён из Git) содержит настоящий API-прогон трёх файлов, а `examples/` — старый
  иллюстративный результат с mock-ответами. Ключи в артефакты не включаются.

## Границы production-готовности

Это усиленный CLI, а не сертифицированный сервис. До обработки больших или внешних
репозиториев нужны расширенный набор эталонных данных и оценка точности лицензий,
политика исключения конфиденциальных файлов, квоты входных токенов, управление секретами
на сервере и проверка зависимостей/образов. Обрабатывайте только специально подготовленный
каталог исходников: инструмент не предназначен для сканирования домашней директории.
Три исходных файла и unit-тесты не доказывают корректность произвольного перевода Rust.
Компиляция подтверждает синтаксис/типы, но не эквивалентность поведения. Автоматическое
возобновление, кэширование и параллельная запись в один output-каталог не поддерживаются.

## Проверка edge cases и порог покрытия

CI требует **не менее 95% строк и отдельно 95% ветвлений**. Общий показатель coverage.py
также должен быть не ниже 95%. Порог проверяется по JSON-отчёту без округления вверх.
Из измерения не исключаются CLI, API-адаптер, ветки ошибок или запуск через `python -m`.
Оплачиваемый API-тест не нужен для достижения порога; unit-тесты используют подмены.

Расширенные проверки охватывают:

| Категория | Позитивные и граничные случаи | Негативные случаи |
|---|---|---|
| Лицензии | MIT-алиасы, GPL only/or-later, пустой список функций, граница 2/3 | Нет лицензии, пробелы вместо имени, конфликт категории |
| Парсеры | async, методы, декораторы, nested, default/variadic/positional-only, JS generators, destructuring, Unicode | Невалидный Python/JS, несогласие с LLM, неподдерживаемый язык |
| API | Строгая схема, перевод, статистика токенов, успешный повтор | HTTP 400/401/403/429/500/503, timeout, connection error, refusal, пустой ответ, невалидная схема, лимит вызовов |
| Файлы | UTF-8 BOM, Unicode-имена, вложенные пути, точная граница размера | Oversize, неверная кодировка, нет/пустой input, отказ чтения, симлинки, выход через symlink, пересечение input/output |
| Запись | Атомарная замена, checkpoint после файла | Disk full/ошибка замены, ошибка отчёта, прерывание следующего файла |
| Rust | Успех проверки, передача отдельного валидатора | Ошибка компиляции, нет rustc/Docker, timeout, сбой очистки контейнера |
| CLI | .env, приоритет окружения, реальные JSON-отчёты с fake LLM, exit codes | Нет/пустой ключ или модель, неположительные лимиты, NaN/Inf timeout, безопасная печать ошибки |

Отсутствующая статистика токенов сохраняется как `null`, а не нулевой расход.
Пустые имена лицензий нормализуются в `null`. UTF-8 BOM удаляется перед анализом, но SHA-256
рассчитывается по исходным байтам. Все обнаруженные симлинки, в том числе битые,
попадают в отчёт как ошибки. Изоляция Rust и дополнительные проверки путей не гарантируют
защиту от одновременной враждебной модификации директорий: используйте выделенные каталоги.

Последнее измерение (2026-11-02): **99.64% строк**, **98.72% ветвлений**,
**99.44% общий показатель**. На Python 3.11, 3.12 и 3.13:
по 138 тестов прошли, 2 пропущены; live API-тест отдельно прошёл.
Непокрыты недостижимый выход из цикла повторов и защитный RuntimeError после него.
HTML-отчёт доступен в артефактах GitHub Actions и в ZIP-архиве проекта.

## LLM evaluations and prompts v2

Added 30 analysis cases and 5 Rust translation cases. Ran 210 real LLM requests
in seven experiments; all results, including failures, are in `evals/reports/`.
Selected v2 + temperature=0 + reasoning_effort=none: 90/90 analysis responses
and 15/15 translations passed. This is observed performance, not a determinism guarantee.
See [evals/README.md](evals/README.md) for methodology, limitations and dataset expansion.
The configured model must support structured output, temperature=0 and reasoning_effort=none.
Verified model: `gpt-5.4-mini-2026-03-17`.

## GitHub CI

Repository: https://github.com/Bogdankozlovskiy/aithena-python-task (private).

- Pull requests to `main` and merge queues: Ruff lint/format, mypy, Flake8, Bandit,
  Python 3.11/3.12/3.13 tests, independent >=95% line/branch thresholds, Docker builds.
- Every push/merge to `main`, plus manual runs on `main`: all pytest tests including
  live OpenAI calls, followed by 30 analysis cases x 3 repeats and 5 Rust cases x 3 repeats.
  These are paid API runs; expected token cost is approximately $0.09 per successful run
  at the model rates recorded in the evaluation report, excluding retries.
- `OPENAI_API_KEY` is stored in GitHub Actions Secrets. It is injected only into the
  API test steps on `main`, never into pull-request jobs, builds or source files.
- `CI required` aggregates all required jobs. Coverage HTML/XML/JSON and JUnit results
  are uploaded as Actions artifacts; live eval reports are retained for 30 days.
- Mypy checks `solution`, `evals`, `scripts`; Ruff and Flake8 also check tests.
  Bandit checks runtime/eval scripts. Its narrowly scoped subprocess suppressions are
  documented inline: fixed argv, no host shell interpolation, isolated generated code.

Local checks:

```bash
uv sync --frozen
uv run ruff check solution tests scripts evals
uv run ruff format --check solution tests scripts evals
uv run mypy
uv run flake8 solution tests scripts evals
uv run bandit -r solution scripts evals -q
```

Branch protection limitation: GitHub rejected required-check enforcement for this private
repository with HTTP 403 (GitHub Pro or public visibility required). The workflow runs
and reports failures, but GitHub does not currently block merges on failed checks.
After upgrading, require the `CI required` check on `main` with up-to-date branches.
The repository remains private.

Latest prompt revision: **v3**, after GitHub CI caught a recurring negative-integer
division error in v2. Analysis instructions are unchanged; translation now includes
the explicit quotient/remainder correction algorithm. Failed v2 CI reports are preserved
in `evals/reports/github-v2-*.json`. Use `--prompt-version v3` for current evaluations.
