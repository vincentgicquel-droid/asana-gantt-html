@echo off
cd /d "%~dp0"
echo Diagnostic : config.env, token, proxy, acces a l'API Asana
echo.
"python\python.exe" generer_gantt.py --test-connexion
echo.
pause
