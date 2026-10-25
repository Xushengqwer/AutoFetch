import os
import sys
import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import win32gui
import win32con
import win32service
import mss
from PIL import Image

sys.stdout.reconfigure(encoding='utf-8')

def attach_desktop():
    try:
        desk = win32service.OpenDesktop('default', 0, False, win32con.GENERIC_ALL)
        desk.SetThreadDesktop()
    except Exception:
        pass

attach_desktop()

def find_game():
    candidates = []
    def cb(h, _):
        if win32gui.IsWindow(h):
            cls = win32gui.GetClassName(h).lower()
            if "ejoy2dx" in cls:
                candidates.append(h)
        return True
    win32gui.EnumWindows(cb, None)
    return candidates[0] if candidates else None

hwnd = find_game()
if not hwnd:
    print("[ERROR] 未找到 S3Client / EJOY2DX 游戏窗口！")
    sys.exit(1)

wrect = win32gui.GetWindowRect(hwnd)
crect = win32gui.GetClientRect(hwnd)
print(f"HWND: {hwnd}")
print(f"WindowRect: {wrect}")
print(f"ClientRect: {crect}")

if wrect[0] <= -30000 or crect[2] <= 100:
    print("[STATUS] MINIMIZED: 游戏窗口目前处于最小化状态！请在任务栏点击游戏将其还原显示在屏幕上。")
    sys.exit(2)

# Get Screen coordinates of client area
pt = win32gui.ClientToScreen(hwnd, (0, 0))
box = {
    "left": pt[0],
    "top": pt[1],
    "width": crect[2] - crect[0],
    "height": crect[3] - crect[1]
}
print(f"[STATUS] ACTIVE: 游戏窗口已在屏幕上显示！物理区域: {box}")

with mss.mss() as sct:
    sct_img = sct.grab(box)
    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
    out_path = r"C:\sanguo_report_collector\debug_live_screen.png"
    img.save(out_path)
    print(f"[SUCCESS] 已成功捕获当前真实画面至: {out_path} (尺寸: {img.size[0]}x{img.size[1]})")
