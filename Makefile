.PHONY: install check start startAndBuild stop remove update

install:
	./scripts/install-docker.sh

check:
	./scripts/install-docker.sh --check

start:
	docker compose up -d --wait

startAndBuild:
	./scripts/install-docker.sh --build

stop:
	docker compose stop

remove:
	docker compose down

update: install
