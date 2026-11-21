@echo off
rem Arrete kanbambu lance par kanbambu-windows.bat (et son serveur).
cd /d "%~dp0.."
if not exist data\serve.pid (
  echo kanbambu ne semble pas lance.
  pause
  exit /b
)
for /f %%p in (data\serve.pid) do taskkill /pid %%p /t /f
del data\serve.pid
