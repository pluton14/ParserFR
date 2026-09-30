#!/bin/bash
# Единая точка входа для однослужебного деплоя (Replit): собирает
# фронтенд статикой (с флагом, скрывающим вкладку «Корпус» — см.
# frontend/src/App.tsx) и запускает FastAPI, который эту статику отдаёт.
#
# Нужные секреты/переменные окружения (задаются в Replit → Secrets):
#   TURSO_DATABASE_URL, TURSO_AUTH_TOKEN — подключение к базе (см. app/db.py)
#   PARSERFR_SCHEDULER_ENABLED=false     — без ночного автосбора
#   PARSERFR_READONLY_DEMO=true          — блокирует запуск сбора через API
set -e

echo "== Сборка фронтенда =="
cd frontend
npm install
VITE_HIDE_CORPUS_TAB=true npm run build
cd ..

echo "== Запуск бэкенда =="
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
