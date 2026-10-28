# Короткие команды для запуска и проверки. Всё то же можно выполнить напрямую через docker compose.
.PHONY: up down restart logs health evals test seed secrets env reset

LOCATION ?= sochi
JURY_AGE_KEY = age1932fu7f70rwyfhaxflyfszz4gf6avxwc95wc0yjwm6zns9tspycqpgdnc0

up:            ## собрать и запустить на http://localhost:8080
	docker compose up --build -d

down:          ## остановить (данные в томе сохраняются)
	docker compose down

restart:       ## пересобрать после изменений кода
	docker compose up --build -d --force-recreate

logs:          ## журнал агента: каждый вызов модели и инструмента
	docker compose logs -f pulse

health:        ## проверить, что сервис жив
	docker compose exec pulse python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health').read().decode())"

evals:         ## прогнать сценарии агента на настоящей модели (отчёт в evals/results/)
	docker compose run --rm -v "$(CURDIR)/evals:/app/evals" pulse python -m evals.run

test:          ## юнит-тесты детерминированного ядра: пакеты, проверка поездки, изменение заказа (без сети и модели)
	uv run pytest -q

seed:          ## заново выгрузить отели локации из OpenStreetMap: make seed LOCATION=kaliningrad
	uv run python scripts/fetch_osm.py $(LOCATION)

secrets:       ## зашифровать .env для жюри в secrets.env.age (нужен age)
	age -r $(JURY_AGE_KEY) -o secrets.env.age .env

env:           ## создать .env из примера, если его ещё нет
	@test -f .env || cp .env.example .env

reset:         ## остановить и стереть данные (пользователи, чаты, заказы, кэш)
	docker compose down -v
