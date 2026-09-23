.PHONY: up down reset logs test native-build native-test

up:
	docker compose up --build

down:
	docker compose down

reset:
	docker compose down -v

logs:
	docker compose logs -f

test:
	docker compose run --rm api pytest -q

native-build:
	cmake -S . -B build
	cmake --build build

native-test: native-build
	ctest --test-dir build --output-on-failure
