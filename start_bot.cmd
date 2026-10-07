@echo off
rem Runs the bot and restarts it 10 seconds after a crash.
rem Exit code 3 means another copy is already running - then stop.
rem The bot writes its own log to logs\bot.log (including crashes).
cd /d "%~dp0"
:loop
".venv\Scripts\python.exe" bot.py >nul 2>&1
if %errorlevel%==3 exit /b 0
timeout /t 10 /nobreak >nul
goto loop
