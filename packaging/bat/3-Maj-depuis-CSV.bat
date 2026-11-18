@echo off
cd /d "%~dp0"
if not exist "python\python.exe" (
  echo Dossier "python" introuvable a cote de ce fichier.
  echo Il faut le package complet Gantt-Asana : extraire le zip EN ENTIER
  echo puis utiliser ce fichier depuis le dossier extrait, pas depuis le zip.
  echo Telechargement : page Releases du depot GitHub asana-gantt-html
  pause
  exit /b 1
)
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
