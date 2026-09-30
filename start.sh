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
# Находка 2026-09-30: Replit подставляет $PORT=5000 в окружение, но
# healthcheck самого Autoscale-деплоя жёстко ждёт порт 8000 (значение
# .replit при ПЕРВОЙ публикации, не обновляется при последующих —
# правка .replit на 5000 эффекта не дала). Слушаем строго 8000.
uvicorn app.main:app --host 0.0.0.0 --port 8000
