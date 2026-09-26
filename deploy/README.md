# Deploy via GHCR (как у mail-eco-product)

1. Коммит с версией: `var 1.0.0` / `ver 1.0.0` / `v1.0.0`
2. CI собирает dist → пушит образы в GHCR → SSH на сервер → `docker compose pull && up`

## Образы

| Image | Назначение |
|-------|------------|
| `ghcr.io/<owner>/mostrans-predict-product-backend` | API + дашборд |
| `ghcr.io/<owner>/mostrans-predict-product-ml` | CatBoost + PyTorch |
| `ghcr.io/<owner>/mostrans-predict-product-emulator` | Поток NDTP |
| `ghcr.io/<owner>/mostrans-predict-product-gateway` | Caddy HTTPS |

## Secrets (Environment `production`)

| Secret | Назначение |
|--------|------------|
| `DOMAIN` | домен сайта |
| `ACME_EMAIL` | Let's Encrypt |
| `DEPLOY_HOST` | IP сервера |
| `DEPLOY_USER` | SSH user |
| `SSH_PRIVATE_KEY` | SSH ключ |
| `GHCR_PULL_TOKEN` | PAT `read:packages` (если private) |
| `DEPLOY_PATH` | `/opt/mostrans-predict` |
| `SSH_PORT` | `22` |

## Если `mkdir: Permission denied`

Пользователь из `DEPLOY_USER` не может писать в `/opt`. Варианты:

**1. Зайти под root (предпочтительно)** — в secrets:
```text
DEPLOY_USER=root
DEPLOY_PATH=/opt/mostrans-predict
```

**2. Один раз создать папку на сервере:**
```bash
ssh root@ВАШ_IP
sudo mkdir -p /opt/mostrans-predict
sudo chown -R ВАШ_ЮЗЕР:ВАШ_ЮЗЕР /opt/mostrans-predict
# если docker требует прав:
sudo usermod -aG docker ВАШ_ЮЗЕР
```

**3. Класть в home (без sudo):**
```text
DEPLOY_PATH=/home/ВАШ_ЮЗЕР/mostrans-predict
```

Локально без деплоя: `docker compose up --build` в корне репозитория.
