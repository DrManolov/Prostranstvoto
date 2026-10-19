@echo off
setlocal
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Start Docker Desktop, wait until it is ready, and run this file again.
  pause
  exit /b 1
)
docker compose version >nul 2>&1
if errorlevel 1 (
  echo Install a current version of Docker Desktop with Docker Compose.
  pause
  exit /b 1
)
docker run --rm --mount "type=bind,source=%cd%,target=/app" -w /app python:3.12-slim-bookworm python scripts/init_local.py --runtime-owner 1000:1000
if errorlevel 1 goto failed
docker compose up -d --build --wait --wait-timeout 180
if errorlevel 1 goto failed
docker compose exec web python manage.py ensure_admin
if errorlevel 1 goto failed
start "" "http://localhost:8000"
echo The application is running on your computer. Use your administrator account to sign in.
pause
exit /b 0
:failed
echo Setup stopped. Keep this window open and report the error shown above.
pause
exit /b 1
