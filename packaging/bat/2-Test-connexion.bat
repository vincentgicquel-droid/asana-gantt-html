@echo off
cd /d "%~dp0"
if not exist "python\python.exe" (
  echo Dossier "python" introuvable a cote de ce fichier.
  echo Il faut le package complet Gantt-Asana : extraire le zip EN ENTIER
  echo puis lancer ce fichier depuis le dossier extrait, pas depuis le zip.
  echo Telechargement : page Releases du depot GitHub asana-gantt-html
  pause
  exit /b 1
)
echo Diagnostic : config.env, token, proxy, acces a l'API Asana
echo.
"python\python.exe" generer_gantt.py --test-connexion
echo.
pause
