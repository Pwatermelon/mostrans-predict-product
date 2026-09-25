# API (кратко)

Интерактивная спецификация: **[/docs](/docs)** (Swagger UI), схема: `/openapi.json`.

## Основные эндпоинты

| Method | Path | Описание |
|--------|------|----------|
| GET | `/api/v1/health` | Статус + режим |
| GET | `/api/v1/snapshot` | Снимок дашборда |
| GET | `/api/v1/incidents` | Алерты |
| GET | `/api/v1/metrics` | Latency |
| POST | `/api/v1/telemetry` | Кадр NDTP |
| POST | `/api/v1/telemetry/batch` | Пакет / датасет |
| POST | `/api/v1/what-if` | What-if доп. ТС |
| WS | `/ws/live` | Live stream |

## ML

| Method | Path | Описание |
|--------|------|----------|
| POST | `http://ml:8001/predict` | Инференс ансамбля |
| GET | `http://ml:8001/info` | Состав моделей |
| GET | `http://ml:8001/docs` | Swagger ML |

PyDoc: модули `backend/app/*`, `ml/models/ensemble.py` — docstrings в коде.
