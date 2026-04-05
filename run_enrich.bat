@echo off
cd /d "%~dp0"
call venv\Scripts\activate
echo Rodando enrich do Tupi (popula as estacoes no banco)...
python main.py ingest tupi
echo Enrich concluido.
pause
