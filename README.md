# МосТранс Предикт

Прогноз опозданий автобусов/троллейбусов за 10–15 минут до срыва графика.

Живёт тремя сервисами в Docker: модель (CatBoost + LSTM), backend с NDTP и пульт диспетчера. Поднимается одной командой.

## Запуск

```bash
docker compose up --build
```

Подождать, пока ML станет healthy (минута–две на холодном старте), и открыть:

| Адрес | Зачем |
|-------|--------|
| http://localhost/ | пульт |
| http://localhost/login | вход (`dispatcher` / `demo`) |
| http://localhost/stats | отчёт по маршрутам |
| http://localhost/admin | залить JSON/CSV в поток |
| http://localhost/docs | Swagger |
| http://localhost/docs/sphinx/ | Sphinx по коду |
| http://localhost/docs/jury.html | коротко для жюри |
| TCP `:9000` | эмулятор телематики |

Прод: https://hackton-test.ru/

## Как устроено

Эмулятор гоняет NDTP по TCP на backend. Backend считает признаки (в т.ч. map matching), дергает ML, пушит состояние в дашборд по WebSocket. Та же `.cbm`, что идёт в `submission.csv`, крутится в `/predict` — без отдельной «офлайн-магии».

```
эмулятор ──TCP──► backend ──HTTP──► ml
                     │
                     └── WS / polling ──► дашборд
```

Стек: Python 3.12, FastAPI, CatBoost, PyTorch, ONNX Runtime, Caddy, Compose.

## Деплой

В сообщении коммита нужна версия (`ver 1.2.0` и т.п.), push в `main` — дальше GHCR и SSH `compose up` на сервер. Секреты и нюансы в [deploy/README.md](deploy/README.md).

## Что смотреть жюри

1. `docker compose up --build` (или сразу прод).
2. Зайти диспетчером → на пульте алерты через ~15 с.
3. Карточка ТС: вероятность, прогноз, причина, сообщение водителю.
4. What-if на маршруте, статистика на `/stats`, заливка истории на `/admin`.
5. `docker compose stop emulator` — сервис не падает, уходит в historical.

Подробнее: [docs/jury.html](docs/jury.html).

## Датасет и сабмит

CSV хакатона лежат в `data/hackathon/`. Готовый `submission.csv` отдаётся с сайта:

https://hackton-test.ru/artifacts/submission.csv

Переобучить внутри контейнера (если надо):

```bash
docker compose exec ml python train_ds.py --data /app/data/hackathon --out /app/artifacts --cache /app/data/cache
docker compose exec ml python predict_validate.py
```

## Папки

```
backend/    API, NDTP, признаки
ml/         обучение и инференс
dashboard/  пульт, статистика, логин
emulator/   поток
deploy/     Caddy и прод-compose
docs/       жюри, Sphinx, perf
```
