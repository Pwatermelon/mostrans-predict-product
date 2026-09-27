# Форма полуфинала — что вставлять

Домен: https://hackton-test.ru

## Поле 1 — рабочая система

https://hackton-test.ru/

(README: `docker compose up --build`)

## Поле 2 — инструкция жюри

https://hackton-test.ru/docs/jury.html

## Поле 3 — документация (Sphinx + Swagger)

Можно одной строкой:

https://hackton-test.ru/docs/sphinx/  
https://hackton-test.ru/docs

- Sphinx/PyDoc: https://hackton-test.ru/docs/sphinx/
- OpenAPI/Swagger: https://hackton-test.ru/docs
- API коротко: https://hackton-test.ru/docs/api.md

## Поле 4 — производительность и доп. фичи

Ссылка:

https://hackton-test.ru/docs/performance.md

Если просят **текст** (не ссылку), вставь ниже:

```
Latency: CatBoost на CPU обычно < 20 мс на кадр; смотреть GET /api/v1/metrics (last_predict_latency_ms). Поток ~12 ТС × 1 Гц, очередь с backpressure.

Надёжность: при stop эмулятора сервис не падает (degraded / historical).

DS: submission.csv и онлайн /predict — одна модель delay_catboost_ds.cbm.

Доп. возможности: map matching; what-if выпуска ТС; сообщения диспетчер→водитель и /driver; авторизация; отчёт /stats; заливка JSON/CSV на /admin; статусы/треки/таблица флота; CatBoost+LSTM (ONNX); деплой GHCR+Caddy.
```

## Ещё полезное

- Вход: https://hackton-test.ru/login — `dispatcher` / `demo`
- Статистика: https://hackton-test.ru/stats
- Данные: https://hackton-test.ru/admin
- Сабмит CSV: https://hackton-test.ru/artifacts/submission.csv
