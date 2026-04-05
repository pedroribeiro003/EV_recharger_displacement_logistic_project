@echo off
cd /d "%~dp0"
call venv\Scripts\activate
echo Iniciando poll do Tupi (intervalo: 5 minutos)...
echo Pressione Ctrl+C para parar.
python main.py ingest tupi --poll
