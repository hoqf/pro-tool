@echo off
REM Starts Pro Tool v2 and opens it in your browser (this PC only), on port 8766.
REM To use it from your phone on the same Wi-Fi, run:  python dsv_web.py --lan
cd /d "%~dp0"
title Pro Tool v2
python dsv_web.py %*
pause
