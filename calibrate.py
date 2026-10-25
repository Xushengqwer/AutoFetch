import os
import sys
import ctypes
import time
from PIL import Image, ImageDraw, ImageFont

# Set Per-Monitor DPI Awareness
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import win32gui
import win32con
import win32process
import win32service

def attach_to_default_desktop():
    try:
        desk = win32service.OpenDesktop('default', 0, False, win32con.GENERIC_ALL)
        desk.SetThreadDesktop()
    except Exception:
        pass

def find_game_window():
    attach_to_default_desktop()
    found_hwnd = None
    candidates = []

    def enum_cb(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd).lower()
            txt = win32gui.GetWindowText(hwnd).lower()
            # Match S3Client EJOY2DX window or title keywords
            if "ejoy2dx" in cls:
                candidates.insert(0, hwnd)
            elif any(k in txt for k in ["三国志", "战略版", "模拟器", "s3client"]):
                candidates.append(hwnd)
        return True

    win32gui.EnumWindows(enum_cb, None)
    return candidates[0] if candidates else None

def get_client_box(hwnd):
    crect = win32gui.GetClientRect(hwnd)
    w = crect[2] - crect[0]
    h = crect[3] - crect[1]
    if w <= 100 or h <= 100:
        return None
    origin = win32gui.ClientToScreen(hwnd, (0, 0))
    return {
        'left': origin[0],
        'top': origin[1],
        'width': w,
        'height': h
    }

def capture_client(hwnd, box):
    import mss
    with mss.mss() as sct:
        monitor = {
            'left': box['left'],
            'top': box['top'],
            'width': box['width'],
            'height': box['height']
        }
        sct_img = sct.grab(monitor)
        img = Image.frombytes('RGB', sct_img.size, sct_img.bgra, 'raw', 'BGRX')
        return img

def run_calibration(out_path=r"C:\sanguo_report_collector\debug_aim.png"):
    hwnd = find_game_window()
    if not hwnd:
        print("[ERROR] 未找到《三国志·战略版》游戏窗口！请先启动游戏并停留在战报页面。")
        return False

    title = win32gui.GetWindowText(hwnd)
    cls = win32gui.GetClassName(hwnd)
    print(f"[INFO] 锁定游戏窗口: HWND={hwnd}, Title=\"{title}\", Class=\"{cls}\"")

    # Restore window if minimized
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.5)
    except Exception as e:
        print("[WARN] 激活窗口异常:", e)

    box = get_client_box(hwnd)
    if not box:
        print("[ERROR] 无法获取游戏客户区大小！窗口可能处于最小化。")
        return False

    W = box['width']
    H = box['height']
    print(f"[INFO] 客户区有效尺寸: 宽度 W={W}, 高度 H={H}")

    img = capture_client(hwnd, box)
    if not img:
        print("[ERROR] 画面截取失败！")
        return False

    draw = ImageDraw.Draw(img)

    # 8 Key Points (Relative percentages based on Gemini 4 baseline)
    # 点位1~6：从上往下6根折叠条中心
    # 点位7：展开大卡片中心（胜/平/败大字）
    # 点位8：右上角【返回】按钮
    points = [
        ("P1: 第6场", 0.500, 0.410, "red"),
        ("P2: 第5场", 0.500, 0.475, "red"),
        ("P3: 第4场", 0.500, 0.540, "red"),
        ("P4: 第3场", 0.500, 0.605, "red"),
        ("P5: 第2场", 0.500, 0.670, "red"),
        ("P6: 第1场", 0.500, 0.735, "red"),
        ("P7: 大卡片中心", 0.500, 0.580, "yellow"),
        ("P8: 返回按钮", 0.935, 0.085, "cyan")
    ]

    r = max(12, int(0.015 * H)) # circle radius
    cross = max(18, int(0.025 * H)) # crosshair length

    print("\n=== 正在标定 8 个关键点位 ===")
    for label, rx, ry, color in points:
        px = int(rx * W)
        py = int(ry * H)
        print(f"  {label:<12} -> 相对: ({rx:.3f}, {ry:.3f}) | 真实像素: ({px:4d}, {py:4d})")

        # Draw circle
        draw.ellipse([(px - r, py - r), (px + r, py + r)], outline=color, width=3)
        # Draw crosshair
        draw.line([(px - cross, py), (px + cross, py)], fill=color, width=2)
        draw.line([(px, py - cross), (px, py + cross)], fill=color, width=2)
        # Draw text label with shadow
        text = f"{label} ({rx:.3f}, {ry:.3f})"
        draw.text((px + r + 5, py - r), text, fill="black")
        draw.text((px + r + 4, py - r - 1), text, fill=color)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path)
    print(f"\n[SUCCESS] 校准靶向图已保存至: {out_path}")
    print("[INFO] 严格执行：绝不点击任何鼠标！仅生成标定图像供人工及 AI 检验。")
    return True

if __name__ == "__main__":
    run_calibration()
