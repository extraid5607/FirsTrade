@echo off
cd /d "%~dp0"
if exist zeropaper-india cd zeropaper-india

set PRICE_SOURCE=live
echo ======================================================
echo           FirsTrade - LIVE MODE
echo     Real Live Market Data (NSE, Yahoo Finance)
echo ======================================================
echo Server is starting...
echo Opening http://localhost:8000 in your browser...
echo.
echo Press Ctrl+C in this window to stop the server.
echo ======================================================
start http://localhost:8000
.\.venv\Scripts\uvicorn.exe app.main:app --port 8000
pause
