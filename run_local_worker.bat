@echo off
cd /d C:\Users\Omid\Desktop\v2ray_platform

set AUTO_LOOP_HEAVY_INTERVAL_HOURS=8
set AUTO_LOOP_HEALTH_INTERVAL_MINUTES=60
set AUTO_LOOP_FAILED_RETRY_MINUTES=15

call .venv\Scripts\activate
python auto_loop.py

pause