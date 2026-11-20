@echo off
rem Lance kanbambu sans fenetre et le garde a jour avec GitHub.
rem Pour le demarrer avec Windows : Win+R, "shell:startup", puis y mettre un raccourci vers ce fichier.
cd /d "%~dp0.."
start "" pythonw tools\serve.py
