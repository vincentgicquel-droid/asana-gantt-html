@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Glisser un export CSV Asana sur ce fichier pour regenerer le planning.
  echo Dans Asana : menu du projet ^> Exporter ^> CSV.
  pause
  exit /b 1
)
echo Mise a jour du planning depuis : %~nx1
"python\python.exe" maj-gantt-depuis-csv.py --csv "%~1"
echo.
pause
