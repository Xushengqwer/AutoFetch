@echo off
cd /d C:\sanguo_report_collector
echo [INFO] 正在启动单步测试...
C:\Python312\python.exe tools\test_round_pipeline.py
echo.
echo ==============================================
echo 测试已结束，请回到对话窗口告知我。
echo ==============================================
pause
