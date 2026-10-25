@echo off
chcp 65001 >nul
cd /d C:\sanguo_report_collector
echo [INFO] 正在启动电脑画面截屏识图与顺序计划生成器...
C:\Python312\python.exe tools\plan_and_audit.py
echo.
echo ==============================================
echo 巡检计划已生成！请回到对话窗口查看结果。
echo ==============================================
pause
