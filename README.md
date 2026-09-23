# EventForge v0.0 — Engineering Foundation

This repository is the first runnable EventForge baseline.

## What works

- PostgreSQL 18.6
- Python 3.14.7 + FastAPI 0.141.1
- React 19.3.0 + TypeScript 7.0.2 + Vite 8.3.0
- C17 build target
- C++20 build target
- Python tests
- C/C++ smoke tests through CTest
- Docker Compose startup
- GitHub Actions CI skeleton

## First run

1. Install Git, Docker Desktop (or Docker Engine + Compose v2), and optionally CMake + a C/C++ compiler.
2. Copy the environment file:

   cp .env.example .env

3. Start the local stack:

   docker compose up --build

4. Open:

   - Frontend: http://localhost:5173
   - API: http://localhost:8000
   - API docs: http://localhost:8000/docs

5. Verify:

   curl http://localhost:8000/health
   curl http://localhost:8000/ready

## Native C/C++ build

mkdir -p build
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure

## Stop

docker compose down

To also delete the local database volume:

docker compose down -v

## v0.0 exit criterion

A clean checkout can start the frontend, API, and PostgreSQL locally and all baseline tests pass.
