.PHONY: up down logs train submit

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

train:
	cd ml && python train.py --out artifacts

submit:
	python scripts/make_submit.py

dist:
	./deploy/build-dist.sh localhost $$(cat VERSION)
