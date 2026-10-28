# Короткие команды для запуска и проверки. Всё то же можно выполнить напрямую через docker compose.
.PHONY: up down restart logs health evals cli env reset

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

cli:           ## поговорить с агентом в терминале, без веб-интерфейса
	docker compose run --rm -it -v "$(CURDIR)/docs:/app/docs" pulse python -m docs.cli

env:           ## создать .env из примера, если его ещё нет
	@test -f .env || cp .env.example .env

reset:         ## остановить и стереть данные (пользователи, чаты, заказы, кэш)
	docker compose down -v
