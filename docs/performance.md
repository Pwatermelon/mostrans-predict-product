# Производительность и доп. возможности

## Latency

- Ориентир инференса ансамбля: **&lt; 50–150 мс** на ТС (CPU), цель потока **&lt; 1–2 с**.
- Метрики в рантайме: `GET /api/v1/metrics` → `last_predict_latency_ms`, `avg_predict_latency_ms`.
- На дашборде latency отображается в шапке.

## Пропускная способность

- Очередь кадров ограничена (backpressure), эмулятор ≈ 12 ТС × 1 Гц.
- Backend обрабатывает кадры асинхронно, WebSocket пушит снимок без накопления UI-очереди.

## Надёжность

- TCP-реконнект к эмулятору с backoff.
- При обрыве: режим `degraded` → replay последнего `sample_telemetry.jsonl` (`historical`).
- Heuristic fallback, если ML недоступен (сервис не падает).

## Холодный старт Docker

1. `ml` — pip + bootstrap моделей (~1–2 мин первый раз).
2. `backend` ждёт healthy ML.
3. `gateway` проксирует на backend.

## Дополнительные возможности (реализовано)

| Фича | Где |
|------|-----|
| Map matching (haversine → сегмент маршрута) | `backend/app/features/engine.py` |
| What-if анализ (+N ТС) | `POST /api/v1/what-if` + панель дашборда |
| Ансамбль CatBoost + PyTorch LSTM | `ml/models/ensemble.py` |
| ONNX-экспорт LSTM | `ml/train.py` → `artifacts/delay_lstm.onnx` |
| Паттерны сбоя + рекомендации | карточка инцидента |
| OpenAPI / Swagger | `/docs` |
| Деплой как mail-eco (GHCR + Caddy + SSH) | `.github/workflows/deploy.yml` |

## GPU

- При наличии CUDA PyTorch автоматически использует GPU (`device=cuda` в `/info` ML).
