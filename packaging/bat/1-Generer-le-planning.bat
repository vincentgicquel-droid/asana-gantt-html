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
echo Generation du planning Gantt depuis Asana
echo Interface : http://localhost:8765 - le navigateur s'ouvre tout seul
echo Laisser cette fenetre ouverte pendant l'utilisation ; la fermer pour arreter.
echo.
"python\python.exe" interface_gantt.py --navigateur
pause
