# testovoe

FastAPI-приложение с двумя эндпоинтами.

## Запуск

```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

## Эндпоинты

- `POST /` — возвращает `Hello, World!`, если передан заголовок `Test: Hello`, иначе `403`.
- `GET /health` — пингует `77.88.8.8`; при успехе `200 OK`, иначе `503`.

## Проверка

```bash
curl -X POST -H 'Test: Hello' localhost:8000/   # Hello, World!
curl -X POST localhost:8000/                    # 403
curl localhost:8000/health                      # OK
```
