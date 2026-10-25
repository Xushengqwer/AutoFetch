import os
import sys
import time
import ctypes
import traceback

log_file = r"C:\sanguo_report_collector\debug_steps\log.txt"
os.makedirs(os.path.dirname(log_file), exist_ok=True)

def log(msg):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    print(line)
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# 1. Self-elevation to Administrator
def ensure_admin():
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False

    if not is_admin:
        log("检测到当前为非管理员权限，正在调起 Windows UAC 提权申请...")
        script = os.path.abspath(__file__)
        python_exe = sys.executable if os.path.exists(sys.executable) else r"C:\Python312\python.exe"
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", python_exe, f'"{script}"', r"C:\sanguo_report_collector", 1
        )
        if ret > 32:
            log("UAC 提权申请已调起，本前置窗口即将关闭。")
            sys.exit(0)
        else:
            log(f"UAC 提权被取消或失败，错误代码: {ret}")
            input("按回车键退出...")
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

def real_click(x, y, hold_sec=0.12):
    """Real human-like click with frame-holding duration to satisfy Direct3D game loops."""
    pyautogui.moveTo(x, y, duration=0.20)
    time.sleep(0.06)
    pyautogui.mouseDown(x, y, button='left')
    time.sleep(hold_sec)
    pyautogui.mouseUp(x, y, button='left')
    time.sleep(0.10)

def main():
    try:
        log("=== 启动三国志·战略版 闭环流水线（含实体重按与滑动避障） ===")
        hwnd = find_game()
        if not hwnd:
            log("[ERROR] 未找到游戏窗口！请确认游戏已打开且未关闭。")
            input("\n按回车键退出...")
            return

        # Restore and foreground
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.5)

        crect = win32gui.GetClientRect(hwnd)
        w = crect[2] - crect[0]
        h = crect[3] - crect[1]
        pt = win32gui.ClientToScreen(hwnd, (0, 0))
        box = {"left": pt[0], "top": pt[1], "width": w, "height": h}
        log(f"客户区尺寸: 宽度={w}, 高度={h}, 屏幕原点=({pt[0]}, {pt[1]})")

        steps_dir = r"C:\sanguo_report_collector\debug_steps"
        os.makedirs(steps_dir, exist_ok=True)

        # ----------------------------------------------------
        # ACTION 1: 点击上方已展开大卡片的【平】字（正中心 Y=360，避开上方地点链接）
        # ----------------------------------------------------
        log("\n[动作 1/4] 瞄准上方大卡片中央【平】字横梁正中 (Y=360)，执行实体点击进全屏...")
        click_x = box["left"] + int(w * 0.50)  # 960
        click_y = box["top"] + int(h * 0.34)   # ~367 in 1080p, dead center of 平

        img1 = capture_box(box)
        aim1 = draw_aim(img1, int(w * 0.50), int(h * 0.34), "1. 点击【平】字正中进全屏战斗回放")
        aim1.save(os.path.join(steps_dir, "step_01_click_ping.png"))

        real_click(click_x, click_y, hold_sec=0.15)
        log(f" -> 已在屏幕坐标 ({click_x}, {click_y}) 执行有效鼠标按下(150ms)+抬起！等待详情加载...")

        time.sleep(1.5)
        img_detail = capture_box(box)
        img_detail.save(os.path.join(steps_dir, "step_02_detail_screen.png"))
        log(" -> 已捕获全屏详情页，存为: debug_steps/step_02_detail_screen.png")

        # ----------------------------------------------------
        # ACTION 2: 点击右上角金色【返回】按键
        # ----------------------------------------------------
        log("\n[动作 2/4] 瞄准右上角金色【返回】按键，准备返回列表...")
        back_x = box["left"] + int(w * 0.945)
        back_y = box["top"] + int(h * 0.038)

        aim2 = draw_aim(img_detail, int(w * 0.945), int(h * 0.038), "2. 点击右上角金色【返回】", "cyan")
        aim2.save(os.path.join(steps_dir, "step_03_click_return.png"))

        real_click(back_x, back_y, hold_sec=0.15)
        log(f" -> 已在屏幕坐标 ({back_x}, {back_y}) 点击【返回】！等待退回列表...")

        time.sleep(1.0)
        img_list = capture_box(box)
        img_list.save(os.path.join(steps_dir, "step_04_returned_list.png"))
        log(" -> 已捕获退回列表页，存为: debug_steps/step_04_returned_list.png")

        # ----------------------------------------------------
        # ACTION 3: 向上轻微平滑滑动，消除底部遮挡！
        # ----------------------------------------------------
        log("\n[动作 3/4] 模拟手指向上平滑拖动列表，将下方的【鏖战3场】拉入屏幕舒适中央...")
        drag_x = box["left"] + int(w * 0.30)
        drag_start_y = box["top"] + int(h * 0.75)
        drag_end_y = box["top"] + int(h * 0.40)

        pyautogui.moveTo(drag_x, drag_start_y, duration=0.2)
        time.sleep(0.08)
        pyautogui.mouseDown(drag_x, drag_start_y, button='left')
        time.sleep(0.12)
        pyautogui.moveTo(drag_x, drag_end_y, duration=0.45)
        time.sleep(0.18)
        pyautogui.mouseUp(drag_x, drag_end_y, button='left')
        time.sleep(0.8)

        img_scrolled = capture_box(box)
        img_scrolled.save(os.path.join(steps_dir, "step_05_scrolled_center.png"))
        log(" -> 滑动已完成，画面已保存为: debug_steps/step_05_scrolled_center.png")

        # ----------------------------------------------------
        # ACTION 4: 点击已拉到中央的【平/胜/败】大卡片进详情！
        # ----------------------------------------------------
        log("\n[动作 4/4] 瞄准已被拉至中央的大卡片【平】字进全屏...")
        # Since we scrolled up by ~35% of H, the round 3 card that was at Y=830 is now around Y=480!
        center_x = box["left"] + int(w * 0.50)
        center_y = box["top"] + int(h * 0.48)

        aim4 = draw_aim(img_scrolled, int(w * 0.50), int(h * 0.48), "4. 点击拉至中央的【平】字进详情")
        aim4.save(os.path.join(steps_dir, "step_06_aim_scrolled_ping.png"))

        real_click(center_x, center_y, hold_sec=0.15)
        log(f" -> 已在屏幕坐标 ({center_x}, {center_y}) 点击中央卡片！等待详情页渲染...")

        time.sleep(1.5)
        img_final_detail = capture_box(box)
        img_final_detail.save(os.path.join(steps_dir, "step_07_round3_full_detail.png"))
        log(" -> 已成功捕获连战第三场全屏详情页: debug_steps/step_07_round3_full_detail.png")

        log("\n🎉 全流程闭环测试顺利完成！所有证据图已全部留存！")
        input("\n测试已全部跑通，按【回车键】即可关闭本窗口...")

    except Exception as e:
        log(f"\n[CRITICAL ERROR] 发生未捕获异常: {e}")
        traceback.print_exc()
        input("\n发生错误，按【回车键】退出...")

if __name__ == "__main__":
    main()
