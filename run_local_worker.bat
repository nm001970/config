@echo off
cd /d C:\Users\Omid\Desktop\v2ray_platform

call .venv\Scripts\activate.bat

python auto_loop.py

echo.
echo Worker finished or stopped.
pause