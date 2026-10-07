@echo off
rem Removes the bot from Windows autostart. The running bot is not stopped.
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\tennis-bot.vbs" 2>nul
echo Autostart removed.
