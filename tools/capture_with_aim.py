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
from PIL import Image, ImageDraw, ImageFont
import asyncio
import winocr

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

def main():
    hwnd = find_game()
    if not hwnd:
        print("[ERROR] 未找到游戏窗口！")
        return

    delay = 3
    print(f"[*] 请在 {delay} 秒内用鼠标点击一下《三国志·战略版》游戏窗口，使其置顶显示在最前...")
    for i in range(delay, 0, -1):
        print(f"    倒计时: {i} 秒...")
        time.sleep(1)

    crect = win32gui.GetClientRect(hwnd)
    w = crect[2] - crect[0]
    h = crect[3] - crect[1]
    pt = win32gui.ClientToScreen(hwnd, (0, 0))

    box = {
        "left": pt[0],
        "top": pt[1],
        "width": w,
        "height": h
    }
    print(f"[INFO] 正在捕获游戏窗口画面: 尺寸 {w}x{h}, 坐标 ({pt[0]}, {pt[1]})...")

    with mss.MSS() as sct:
        sct_img = sct.grab(box)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

    # Run OCR to detect key visual elements
    print("[INFO] 正在调用 Windows 原生 OCR 识别关键文字点位...")
    try:
        async def run_ocr():
            return await winocr.recognize_pil(img, lang="zh-Hans")
        ocr_res = asyncio.run(run_ocr())
        lines = ocr_res.lines
    except Exception as e:
        print("[WARN] OCR 识别异常:", e)
        lines = []

    draw = ImageDraw.Draw(img)
    r = 16
    cross = 24

    def draw_crosshair(px, py, label, color="red"):
        draw.ellipse([(px - r, py - r), (px + r, py + r)], outline=color, width=3)
        draw.line([(px - cross, py), (px + cross, py)], fill=color, width=2)
        draw.line([(px, py - cross), (px, py - cross)], fill=color, width=2)
        draw.line([(px, py - cross), (px, py + cross)], fill=color, width=2)
        draw.text((px + r + 6, py - r - 2), label, fill="black")
        draw.text((px + r + 5, py - r - 3), label, fill=color)

    # 1. Top-Right Return Button
    back_found = False
    for l in lines:
        if "返回" in l.text.replace(' ', '') and l.words:
            bx = int(l.words[0].bounding_rect.x + l.words[0].bounding_rect.width // 2)
            by = int(l.words[0].bounding_rect.y + l.words[0].bounding_rect.height // 2)
            draw_crosshair(bx, by, "【返回】按键 (OCR精确命中)", "cyan")
            back_found = True
            break
    if not back_found:
        draw_crosshair(int(w * 0.935), int(h * 0.045), "【返回】按键 (比例兜底: 93.5%, 4.5%)", "cyan")

    # 2. Battle rows / cards detected
    card_idx = 1
    for l in lines:
        txt = l.text.replace(' ', '')
        # Detect battle items, round items, or timestamps
        if any(k in txt for k in ["鏖战", "斩获", "2026", "26年", "场", "战报"]) and l.words:
            # We want safe area: left 25%~35% of width
            cy = int(l.words[0].bounding_rect.y + l.words[0].bounding_rect.height // 2)
            cx = int(w * 0.32)
            draw_crosshair(cx, cy, f"目标{card_idx}: {txt[:10]} (安全点击区)", "red")
            card_idx += 1

    out_file = r"C:\sanguo_report_collector\debug_aim_live.png"
    img.save(out_file)
    print(f"\n[SUCCESS] 校准靶向图已保存至: {out_file}")
    print("[INFO] 严格执行：绝不盲目点击鼠标！纯画靶自检。")

if __name__ == "__main__":
    main()
