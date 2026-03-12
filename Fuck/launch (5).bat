@echo off
chcp 65001 >nul
title MARLOW — Strategic Intelligence System
color 0A
mode con: cols=72 lines=45
cd /d "C:\"

cls
echo.
echo.
echo   ══════════════════════════════════════════════════════════════════════
echo.
echo     ███╗   ███╗ █████╗ ██████╗ ██╗      ██████╗ ██╗    ██╗
echo     ████╗ ████║██╔══██╗██╔══██╗██║     ██╔═══██╗██║    ██║
echo     ██╔████╔██║███████║██████╔╝██║     ██║   ██║██║ █╗ ██║
echo     ██║╚██╔╝██║██╔══██║██╔══██╗██║     ██║   ██║██║███╗██║
echo     ██║ ╚═╝ ██║██║  ██║██║  ██║███████╗╚██████╔╝╚███╔███╔╝
echo     ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝  ╚══╝╚══╝
echo.
echo   ══════════════════════════════════════════════════════════════════════
echo      Strategic Continuity Intelligence  //  Personal Edition
echo   ══════════════════════════════════════════════════════════════════════
echo.

timeout /t 1 /nobreak >nul

echo   [ 01/06 ]  Initializing council nodes.................. OK
timeout /t 1 /nobreak >nul
echo   [ 02/06 ]  Loading behavioral vault.................... OK
timeout /t 1 /nobreak >nul
echo   [ 03/06 ]  Loading session state....................... OK
timeout /t 1 /nobreak >nul
echo   [ 04/06 ]  Connecting to Groq API...................... OK
timeout /t 1 /nobreak >nul
echo   [ 05/06 ]  Running crash alert scan.................... OK
timeout /t 1 /nobreak >nul
echo   [ 06/06 ]  Starting background intelligence daemon..... OK
timeout /t 1 /nobreak >nul

echo.
echo   ══════════════════════════════════════════════════════════════════════
echo.

"C:\Users\rodre\AppData\Local\Python\pythoncore-3.14-64\python.exe" marlow.py %*

echo.
echo   ══════════════════════════════════════════════════════════════════════
echo   [  MARLOW  ]  Session closed. MORRO says you'll be back.
echo   ══════════════════════════════════════════════════════════════════════
echo.
pause
