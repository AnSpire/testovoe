# Пункты c + d1 — Dockerfile и docker-compose (app + prometheus + alertmanager)

## Задача

- **c** — Dockerfile для приложения, собрать образ.
- **d1** — docker-compose с app + prometheus + alertmanager, настроить мониторинг.

## Расположение

В отличие от Lab 1 (VM-лаба, живёт в `infra/nat-lab/`), этот кусок — чистый
Docker на хосте, без VM, поэтому лежит в **корне репозитория**, рядом с
`main.py`/`pyproject.toml` (там же естественный build-контекст):

```
Dockerfile
docker-compose.yml
monitoring/
  prometheus.yml
  alerts.yml
  alertmanager.yml
reports/
  c-d1-docker.md   (этот файл)
```

## Изменение в main.py: эндпоинт /metrics

У приложения не было ничего, что реально можно скрейпить Prometheus'ом
(текстовый формат метрик на `/metrics`). Добавлена библиотека
[`prometheus-fastapi-instrumentator`](https://github.com/trallnag/prometheus-fastapi-instrumentator)
(`uv add prometheus-fastapi-instrumentator`) — две строчки в `main.py`:

```python
app = FastAPI()
Instrumentator().instrument(app).expose(app)
```

Это добавляет `GET /metrics` с метриками количества запросов
(`http_requests_total`), длительности (`http_request_duration_seconds`) и
т.д. — без изменения требуемого поведения `POST /` и `GET /health` из
пункта a.

## Dockerfile

Многостадийная сборка на базе официального образа `uv` (astral-sh):

1. **Стадия `builder`** (`ghcr.io/astral-sh/uv:python3.13-bookworm-slim`):
   - сначала копируются только `pyproject.toml`/`uv.lock` и `uv sync
     --frozen --no-install-project --no-dev` — зависимости кэшируются
     отдельным слоем, независимо от правок `main.py`;
   - затем копируется `main.py` и `uv sync` дособирает проект.
2. **Финальная стадия** (`python:3.13-slim-bookworm`):
   - `iputils-ping` — нужен `/health`, который дёргает системный `ping`;
   - отдельный системный пользователь `appuser` (без шелла) — тот же
     принцип "не от root", что и в пункте b;
   - из `builder` копируется `/app` (включая `.venv`) с `chown appuser`;
   - `CMD uvicorn main:app --host 0.0.0.0 --port 8000`.

`ping` работает от непривилегированного пользователя в контейнере без
дополнительных `cap_add`, так как `CAP_NET_RAW` входит в дефолтный набор
capabilities Docker, а `iputils-ping` в Debian использует file capabilities
(не setuid-root).

## docker-compose.yml

Три сервиса в одной bridge-сети `lab`:

- **app** — собирается из локального `Dockerfile`, порт `8000`.
- **prometheus** (`prom/prometheus:v2.54.1`) — конфиг и правила алертов
  монтируются из `monitoring/`, порт `9090`.
- **alertmanager** (`prom/alertmanager:v0.27.0`) — конфиг из `monitoring/`,
  порт `9093`.

## Мониторинг

`monitoring/prometheus.yml` — скрейпит `app:8000` (job `app`) каждые 15s,
подключён к `alertmanager:9093`, подгружает правила из `alerts.yml`.

`monitoring/alerts.yml` — два правила:
- **AppDown** — `up{job="app"} == 0` дольше 30s (приложение недоступно
  для скрейпа).
- **AppHighErrorRate** — доля 5xx-ответов выше 10% за 5 минут.

`monitoring/alertmanager.yml` — минимальный роутинг на единственный
receiver `default`, который **ничего никуда не отправляет** (нет
настроенного канала — slack/email/webhook). Для лабы этого достаточно —
видно, что алерты реально долетают до Alertmanager (через его UI
`:9093` или API `/api/v2/alerts`), подключение настоящего канала
уведомлений — это просто доп. блок `receivers` в этом файле.

## Образ в Docker Hub

Образ собирается и пушится как `anspire/testovoe-app:latest`
(docker-compose.yml уже содержит `image: anspire/testovoe-app:latest`,
поэтому `docker compose build` сразу тэгирует правильно):

```bash
docker compose build
docker push anspire/testovoe-app:latest
```

Используется напрямую в Lab 3 (Kubernetes) — кластер тянет этот же образ
из Docker Hub, без пересборки и без своего registry.

## Проверка результата

```bash
$ docker compose up -d
$ docker compose ps
NAME                      STATUS
testovoe-app-1            Up
testovoe-prometheus-1     Up
testovoe-alertmanager-1   Up

$ curl -X POST -H 'Test: Hello' http://localhost:8000/
Hello, World!

$ curl -o /dev/null -w 'HTTP %{http_code}\n' -X POST http://localhost:8000/
HTTP 403

$ curl http://localhost:8000/health
OK

$ curl http://localhost:8000/metrics | head
# HELP python_gc_objects_collected_total ...
...

$ curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health}'
{"job": "app", "health": "up"}

$ curl http://localhost:9090/api/v1/rules | jq '.data.groups[].rules[].name'
"AppDown"
"AppHighErrorRate"

$ curl -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:9093/-/healthy
HTTP 200
```

Все компоненты пункта c (образ собирается и работает) и d1 (compose-стек
с мониторингом, Prometheus реально скрейпит `app` и знает про алерт-правила,
Alertmanager жив и сконфигурирован) подтверждены.
