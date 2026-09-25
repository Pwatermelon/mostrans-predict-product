# МосТранс Предикт

ИИ-система раннего прогнозирования изменений графика наземного транспорта Москвы (**горизонт 10–15 минут**).

Три модуля: **ML-ядро** (CatBoost + PyTorch) · **Backend** (NDTP, оркестрация, OpenAPI) · **BI-дашборд** диспетчера.

## Быстрый старт

```bash
docker compose up --build
```

| URL | Что |
|-----|-----|
| http://localhost/ | Диспетчерский пульт |
| http://localhost/docs | Swagger Backend |
| http://localhost:8001/docs | Swagger ML |
| http://localhost/docs/jury.html | Инструкция жюри |
| TCP `:9000` | Эмулятор NDTP (JSONL) |

Первый старт ML: ~1–2 мин (обучение bootstrap-моделей).

## Архитектура

```
Эмулятор NDTP ──TCP──► Backend ──HTTP──► ML (CatBoost+LSTM/ONNX)
                         │
                         ├── признаки + map matching
                         ├── алерты 10–15 мин
                         └── WebSocket ──► BI Dashboard
```

## Стек

- Python 3.12+, FastAPI, PyTorch, CatBoost, ONNX Runtime
- Docker Compose, Caddy
- Деплой как у mail-eco: версия в коммите → GHCR → SSH `compose up`

## Деплой (production)

В коммите должна быть версия:

```text
var 1.0.0
ver 1.0.0
v1.0.0
```

```bash
git commit -m "release ver 1.0.0"
git push origin main
```

Pipeline: dist → образы GHCR (`-backend`, `-ml`, `-emulator`, `-gateway`) → artifact → на сервере `docker compose pull && up`.

Secrets (Environment `production`): `DOMAIN`, `ACME_EMAIL`, `DEPLOY_HOST`, `DEPLOY_USER`, `SSH_PRIVATE_KEY`, опционально `GHCR_PULL_TOKEN`, `DEPLOY_PATH`, `SSH_PORT`.

Подробнее: [deploy/README.md](deploy/README.md).

## Для жюри

1. `docker compose up --build`
2. Открыть дашборд — алерты появятся через ~10–20 с (эмулятор сам создаёт сбои)
3. Клик по карточке инцидента: вероятность, опоздание, причина, рекомендация
4. What-if: выбрать маршрут → «Рассчитать»
5. Swagger: `GET /api/v1/health`
6. Проверка деградации: `docker compose stop emulator` — сервис жив, режим historical

Полная инструкция: [docs/jury.html](docs/jury.html) · производительность: [docs/performance.md](docs/performance.md).

## Датасет хакатона (DS)

Датасет лежит в репозитории: `data/hackathon/` (~84 МБ CSV, без docker-образа эмулятора).
В Docker он **вшит в образы** `ml` и `backend` вместе с готовым `submission.csv`.

После раскатки сабмит доступен по URL (руками на сервер лезть не нужно):

```text
https://<DOMAIN>/artifacts/submission.csv
```

Локально:

```bash
docker compose up --build
open http://localhost/artifacts/submission.csv
```

Переобучение внутри контейнера (опционально):

```bash
docker compose exec ml python train_ds.py --data /app/data/hackathon --out /app/artifacts --cache /app/data/cache
docker compose exec ml python predict_validate.py
```

## Структура

```
backend/     API, NDTP, признаки, оркестрация
ml/          ансамбль, train, serve
dashboard/   BI пульт
emulator/    поток NDTP
deploy/      Caddy, compose prod, build-dist
docs/        жюри, API, performance
```
