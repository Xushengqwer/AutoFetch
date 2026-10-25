import os
import sys
import time
import ctypes

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
    r = 20
    cross = 30
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

    crect = win32gui.GetClientRect(hwnd)
    w = crect[2] - crect[0]
    h = crect[3] - crect[1]
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    box = {"left": pt[0], "top": pt[1], "width": w, "height": h}

    os.makedirs(r"C:\sanguo_report_collector\debug_steps", exist_ok=True)

    # STEP 1: Capture current screen and mark target click point
    # We want to click the expanded card safely on hero SP曹操/SP卢植: (X = 430, Y = 380) in client coordinates
    click_rel_x = int(w * 0.23) # ~441
    click_rel_y = int(h * 0.35) # ~378
    screen_x = box["left"] + click_rel_x
    screen_y = box["top"] + click_rel_y

    print(f"[*] 准备点击展开卡片英雄区域: 相对坐标 ({click_rel_x}, {click_rel_y}) | 屏幕坐标 ({screen_x}, {screen_y})")

    img_before = capture_box(box)
    aim_img1 = draw_aim(img_before, click_rel_x, click_rel_y, "Step 1: 点击卡片进详情 (安全英雄区)")
    aim_img1.save(r"C:\sanguo_report_collector\debug_steps\step_01_before_click_card.png")
    print(" -> 已保存: debug_steps/step_01_before_click_card.png")

    # STEP 2: Execute Click
    pyautogui.moveTo(screen_x, screen_y, duration=0.2)
    time.sleep(0.05)
    pyautogui.click()
    print(" -> 已执行鼠标点击")

    # STEP 3: Wait 0.8s and capture result
    time.sleep(0.8)
    img_after = capture_box(box)
    img_after.save(r"C:\sanguo_report_collector\debug_steps\step_02_after_click_card.png")
    print(" -> 已保存: debug_steps/step_02_after_click_card.png")

if __name__ == "__main__":
    main()
