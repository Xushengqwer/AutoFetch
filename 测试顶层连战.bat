@echo off
chcp 65001 >nul
cd /d C:\sanguo_report_collector
echo [INFO] 正在启动【顶层 3 场像素级视觉锁定实测】...
C:\Python312\python.exe tools\test_top3_solid.py
echo.
echo ==============================================
echo 实测已结束，请回到对话窗口告知结果。
echo ==============================================
pause
