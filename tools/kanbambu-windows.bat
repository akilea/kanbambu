@echo off
rem Lance kanbambu sans fenetre et le garde a jour avec GitHub.
rem Pour le demarrer avec Windows : Win+R, "shell:startup", puis y mettre un raccourci vers ce fichier.
cd /d "%~dp0.."
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw tools\serve.py
) else (
  rem pas de pythonw : meme chose avec python, dans une fenetre reduite
  start "kanbambu" /min python tools\serve.py
)
rem verifie qu'il repond (port 8642 par defaut)
timeout /t 6 /nobreak >nul
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://localhost:8642/api/version -TimeoutSec 5 | Out-Null; Write-Host 'kanbambu tourne : http://localhost:8642' } catch { Write-Host 'kanbambu ne repond pas. Lance  python tools\serve.py  pour voir l erreur, ou regarde data\kanbambu.log'; pause }"
