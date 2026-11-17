@echo off
cd /d "%~dp0"
echo Generation du planning Gantt depuis Asana
echo Interface : http://localhost:8765 (le navigateur s'ouvre tout seul)
echo Laisser cette fenetre ouverte pendant l'utilisation ; la fermer pour arreter.
echo.
"python\python.exe" interface_gantt.py --navigateur
pause
