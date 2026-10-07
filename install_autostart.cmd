@echo off
rem Adds the bot to Windows autostart for the current user (no admin rights needed)
rem and starts it right away. To remove: uninstall_autostart.cmd
set "TARGET=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\tennis-bot.vbs"
> "%TARGET%" echo CreateObject("WScript.Shell").Run """%~dp0start_hidden.vbs""", 0, False
echo Autostart installed: %TARGET%
wscript "%~dp0start_hidden.vbs"
echo Bot started in the background. Log: %~dp0logs\bot.log
