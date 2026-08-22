@echo off
title Keithley 6517 Control Studio
cd /d "%~dp0"
echo Iniciando Keithley 6517 Control Studio...
python -m src.main
if %errorlevel% neq 0 (
    echo.
    echo Ocorreu um erro ao executar a aplicacao.
    pause
)
