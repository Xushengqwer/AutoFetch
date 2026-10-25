import os
import sys
import time
import ctypes

# 1. Self-elevation to Administrator
def ensure_admin():
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False

    if not is_admin:
        print("[INFO] 当前非管理员，正在申请管理员提权 (UAC)...")
        script = os.path.abspath(__file__)
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}"', None, 1
        )
        if ret > 32:
            print("[INFO] 已调起管理员提权进程。")
            sys.exit(0)
        else:
            print("[ERROR] 提权申请失败，错误码:", ret)
            sys.exit(1)

ensure_admin()

# Per-Monitor DPI Awareness
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import win32gui
import win32con
import win32service
import mss
from PIL import Image, ImageDraw
import pyautogui

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

def capture_box(box):
    with mss.MSS() as sct:
        sct_img = sct.grab(box)
        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

def draw_aim(img, px, py, label, color="red"):
    img_copy = img.copy()
    draw = ImageDraw.Draw(img_copy)
    r = 24
    cross = 36
    draw.ellipse([(px - r, py - r), (px + r, py + r)], outline=color, width=4)
    draw.line([(px - cross, py), (px + cross, py)], fill=color, width=3)
    draw.line([(px, py - cross), (px, py + cross)], fill=color, width=3)
    draw.text((px + r + 8, py - r - 4), label, fill="black")
    draw.text((px + r + 7, py - r - 5), label, fill=color)
    return img_copy

def main():
    hwnd = find_game()
    if not hwnd:
        print("[ERROR] 未找到游戏窗口！")
        return

    # Activate game window
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.4)

    crect = win32gui.GetClientRect(hwnd)
    w = crect[2] - crect[0]
    h = crect[3] - crect[1]
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    box = {"left": pt[0], "top": pt[1], "width": w, "height": h}

    steps_dir = r"C:\sanguo_report_collector\debug_steps"
    os.makedirs(steps_dir, exist_ok=True)

    # 1. Aim and click the big outcome word "平" at center: (X = 960, Y = 400)
    click_x = box["left"] + int(w * 0.50)
    click_y = box["top"] + int(h * 0.37) # ~400 in 1080p

    img_before = capture_box(box)
    aim1 = draw_aim(img_before, int(w * 0.50), int(h * 0.37), "点击大卡片【平】字进全屏详情")
    aim1.save(os.path.join(steps_dir, "step_01_click_ping.png"))

    # Click
    pyautogui.moveTo(click_x, click_y, duration=0.25)
    time.sleep(0.08)
    pyautogui.click()

    # Wait for Detail View to animate in
    time.sleep(1.2)
    img_detail = capture_box(box)
    img_detail.save(os.path.join(steps_dir, "step_02_detail_screen.png"))

    # 2. Aim and click the top-right Return button
    back_x = box["left"] + int(w * 0.945)
    back_y = box["top"] + int(h * 0.038)

    aim2 = draw_aim(img_detail, int(w * 0.945), int(h * 0.038), "点击右上角【返回】按键", "cyan")
    aim2.save(os.path.join(steps_dir, "step_03_click_return.png"))

    # Click return
    pyautogui.moveTo(back_x, back_y, duration=0.25)
    time.sleep(0.08)
    pyautogui.click()

    # Wait for list view to return
    time.sleep(0.8)
    img_after = capture_box(box)
    img_after.save(os.path.join(steps_dir, "step_04_returned_list.png"))

    with open(os.path.join(steps_dir, "result.txt"), "w", encoding="utf-8") as f:
        f.write("COMPLETED_SUCCESSFULLY\n")
    print("[SUCCESS] 单步闭环测试执行完毕！")

if __name__ == "__main__":
    main()
