@echo off
title MARLOW — Oracle Continuity Interface
chcp 65001 >nul

:: Move to script directory
cd /d "C:\Fuck"

:: Find Python
set "PYCMD="
where py >nul 2>&1 && set "PYCMD=py"
if "%PYCMD%"=="" where python >nul 2>&1 && set "PYCMD=python"
if "%PYCMD%"=="" set "PYCMD=C:\Users\rodre\AppData\Local\Python\pythoncore-3.14-64\python.exe"

:: Run the cinematic boot sequence (boot_screen hands off to marlow.py internally)
"%PYCMD%" boot_screen.py

:: Keep window open after MARLOW exits
echo.
echo.
echo   ══════════════════════════════════════════════════════════════════════
echo      [ MARLOW ]  The oracle withdraws. The thread remains.
echo   ══════════════════════════════════════════════════════════════════════
echo.

cmd /k
