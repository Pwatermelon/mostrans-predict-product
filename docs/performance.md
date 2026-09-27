# Производительность и честность DS

## Потоковая ML = сабмит

- Модель сабмита: `delay_catboost_ds.cbm` (`MODEL_ID=delay_catboost_ds`).
- Онлайн: backend собирает `FEATURE_COLS` → `POST ml:8001/predict` → та же `.cbm`.
- LSTM — только дополнительный сигнал вероятности / fallback, **не** источник `submission.csv`.
- Проверка: `scripts/verify_stream_parity.py` (max |offline−online| &lt; 0.05 с).

## Latency

- Ориентир инференса CatBoost DS: **&lt; 20 мс** на точку (CPU).
- Метрики: `GET /api/v1/metrics` → `last_predict_latency_ms`, `ml_model`.

## Пропускная способность и надёжность

- Очередь кадров с backpressure; эмулятор ≈ 12 ТС × 1 Гц.
- TCP-реконнект; degraded → historical replay; heuristic если ML недоступен.

## Доп. возможности

| Фича | Где |
|------|-----|
| Map matching | `backend/app/features/engine.py` |
| What-if | `POST /api/v1/what-if` |
| Сообщения Д→В | `POST /api/v1/messages`, `/driver` |
| Авторизация | `/login` · `dispatcher`/`demo`, `driver-1000`…`/demo` |
| Статистика маршрутов | `/stats` · день/неделя/месяц, A/B, остановки |
| Панель загрузки данных | `/admin` · JSON/CSV/JSONL → поток |
| Статусы / трек / таблица | дашборд |
| Ансамбль CatBoost + LSTM | `ml/models/ensemble.py` |
| ONNX LSTM (aux) | `ml/artifacts` |
| Деплой GHCR | `.github/workflows/deploy.yml` |
