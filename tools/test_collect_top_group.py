import os
import sys
import time
import re
import ctypes
import traceback

# 1. Self-elevation to Administrator
def ensure_admin():
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False

    if not is_admin:
        script = os.path.abspath(__file__)
        python_exe = sys.executable if os.path.exists(sys.executable) else r"C:\Python312\python.exe"
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", python_exe, f'"{script}"', r"C:\sanguo_report_collector", 1
        )
        sys.exit(0)

ensure_admin()

# 2. Per-Monitor DPI Awareness
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import win32gui
import win32con
import win32service
import mss
from PIL import Image
import pyautogui
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

def get_box(hwnd):
    crect = win32gui.GetClientRect(hwnd)
    w = crect[2] - crect[0]
    h = crect[3] - crect[1]
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    return {"left": pt[0], "top": pt[1], "width": w, "height": h}

def capture_box(box):
    with mss.MSS() as sct:
        sct_img = sct.grab(box)
        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

def real_click(x, y, hold_sec=0.15):
    pyautogui.moveTo(x, y, duration=0.15)
    time.sleep(0.05)
    pyautogui.mouseDown(x, y, button='left')
    time.sleep(hold_sec)
    pyautogui.mouseUp(x, y, button='left')
    time.sleep(0.12)

def ocr_lines(pil_img):
    async def _ocr():
        return await winocr.recognize_pil(pil_img, lang="zh-Hans")
    res = asyncio.run(_ocr())
    return res.lines

def is_in_detail(screen_pil):
    lines = ocr_lines(screen_pil)
    joined = "".join([l.text.replace(' ', '') for l in lines])
    return ("战斗回放" in joined or "回放" in joined or "承天靖世" in joined or "战果" in joined)

def return_to_list(hwnd, box):
    back_x = box["left"] + int(box["width"] * 0.945)
    back_y = box["top"] + int(box["height"] * 0.038)
    for attempt in range(5):
        cur = capture_box(box)
        if not is_in_detail(cur):
            return True
        print(f" -> 正在点击右上角【返回】 (第 {attempt+1} 次尝试)...")
        real_click(back_x, back_y, hold_sec=0.15)
        for _ in range(8):
            time.sleep(0.2)
            chk = capture_box(box)
            if not is_in_detail(chk):
                time.sleep(0.4)
                return True
    return False

def enter_detail_with_retry(box, target_x, target_y):
    """Try clicking the outcome character with micro-offsets until full-screen detail is active."""
    offsets = [0, -35, 35, -70, 70]
    for dy in offsets:
        cx = target_x
        cy = target_y + dy
        real_click(cx, cy, hold_sec=0.15)
        time.sleep(1.2)
        cur = capture_box(box)
        if is_in_detail(cur):
            return cur
    return None

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|@\s]', '_', name)

def extract_opponent_from_detail(screen_pil):
    """OCR the detail screen to extract the enemy name displayed on the right side."""
    lines = ocr_lines(screen_pil)
    candidates = []
    for l in lines:
        t = l.text.replace(' ', '')
        if not l.words:
            continue
        x = l.words[0].bounding_rect.x
        y = l.words[0].bounding_rect.y
        if x > 850 and 100 < y < 500:
            if re.match(r'^[\d/／]+$', t):
                continue
            if not any(k in t for k in ['战斗', '回放', '地点', '士气', '我方', '返回', '详情', '统计', '伤兵', '敌方', '枪兵', '弓兵', '骑兵', '盾兵']):
                candidates.append((y, t))
    if candidates:
        candidates.sort(key=lambda item: item[0])
        return sanitize_filename(candidates[0][1])
    return "未知对手"

def main():
    print("=" * 60)
    print("  【三国志·战略版】最顶层【鏖战 3 场】无遗漏攻坚采集测试")
    print("=" * 60)

    hwnd = find_game()
    if not hwnd:
        print("[ERROR] 未找到游戏窗口 S3Client / EJOY2DX！请确认游戏已打开且未关闭。")
        input("\n按回车退出...")
        return

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.6)

    box = get_box(hwnd)
    W = box["width"]
    H = box["height"]

    out_dir = r"C:\sanguo_report_collector\战报导出\测试顶层连战全采"
    os.makedirs(out_dir, exist_ok=True)

    print(f"窗口物理原点: ({box['left']}, {box['top']}), 尺寸: {W}x{H}")
    print(f"战报保存目录: {out_dir}\n")

    # If currently in detail view, return to list first
    init_screen = capture_box(box)
    if is_in_detail(init_screen):
        print("[INIT] 当前处于详情页，先返回列表...")
        return_to_list(hwnd, box)
        time.sleep(0.5)

    # ---------------------------------------------------------
    # STEP 1: 采集当前顶部已展开的大卡片（第三场: 龍漢丨钧宸）
    # ---------------------------------------------------------
    print("=" * 50)
    print("▶ 步骤 1/3: 采集最顶部已展开的场次（第三场）...")
    screen = capture_box(box)
    lines = ocr_lines(screen)

    exp_date = "2026-10-24_162429"
    exp_date_y = 627
    for l in lines:
        t = l.text.replace(' ', '')
        m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', t)
        if m and l.words:
            yr, mon, d, hr, mi, sec = m.groups()
            exp_date = f"20{yr}-{int(mon):02d}-{int(d):02d}_{int(hr):02d}{int(mi):02d}{int(sec):02d}"
            exp_date_y = int(l.words[0].bounding_rect.y + l.words[0].bounding_rect.height / 2)
            break

    # Calculate center of outcome character ("平")
    click_x = box["left"] + int(W * 0.50)
    click_y = box["top"] + max(int(0.18 * H), exp_date_y - int(0.12 * H))

    print(f" -> 瞄准顶部卡片中央【平】字 (坐标: {click_x}, {click_y})，准备进入全屏详情...")
    detail_1 = enter_detail_with_retry(box, click_x, click_y)
    if detail_1:
        opp_name = extract_opponent_from_detail(detail_1)
        save_path = os.path.join(out_dir, f"1_第三场_平_{opp_name}_{exp_date}.png")
        detail_1.save(save_path)
        print(f" -> ✅ 成功保存第 1 张战报: {os.path.basename(save_path)}")
        print(f"    对手识别为: 【{opp_name}】")
    else:
        print(" -> ❌ 进入第三场详情页失败！")

    print(" -> 正在返回战报列表...")
    return_to_list(hwnd, box)
    time.sleep(0.8)

    # ---------------------------------------------------------
    # STEP 2: 展开并采集第二场（第二场: 龍漢丨鲲鵬）
    # ---------------------------------------------------------
    print("\n" + "=" * 50)
    print("▶ 步骤 2/3: 展开并采集【第二场】（龍漢丨鲲鵬）...")
    screen = capture_box(box)
    lines = ocr_lines(screen)

    row2_y = None
    for l in lines:
        t = l.text.replace(' ', '')
        if "第二场" in t and l.words:
            row2_y = int(l.words[0].bounding_rect.y + l.words[0].bounding_rect.height / 2)
            break

    if not row2_y:
        print("[WARN] 未在画面中找到【第二场】条目，使用基准坐标 694...")
        row2_y = 694

    print(f" -> 点击【第二场】折叠条 (X={int(W*0.35)}, Y={row2_y}) 展开手风琴...")
    real_click(box["left"] + int(W * 0.35), box["top"] + row2_y, hold_sec=0.15)
    time.sleep(1.2)  # Wait for accordion expansion animation

    # Locate the newly expanded card's date or outcome character
    screen_exp2 = capture_box(box)
    lines_exp2 = ocr_lines(screen_exp2)
    date2_y = None
    date2_str = "Round2"
    for l in lines_exp2:
        t = l.text.replace(' ', '')
        m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', t)
        if m and l.words:
            yr, mon, d, hr, mi, sec = m.groups()
            date2_str = f"20{yr}-{int(mon):02d}-{int(d):02d}_{int(hr):02d}{int(mi):02d}{int(sec):02d}"
            date2_y = int(l.words[0].bounding_rect.y + l.words[0].bounding_rect.height / 2)
            break

    if date2_y:
        click2_y = box["top"] + max(int(0.18 * H), date2_y - int(0.12 * H))
    else:
        click2_y = box["top"] + int(0.48 * H)

    click2_x = box["left"] + int(W * 0.50)
    print(f" -> 瞄准第二场大卡片中央【胜】字 (坐标: {click2_x}, {click2_y})，准备进入全屏详情...")
    detail_2 = enter_detail_with_retry(box, click2_x, click2_y)
    if detail_2:
        opp_name = extract_opponent_from_detail(detail_2)
        save_path = os.path.join(out_dir, f"2_第二场_胜_{opp_name}_{date2_str}.png")
        detail_2.save(save_path)
        print(f" -> ✅ 成功保存第 2 张战报: {os.path.basename(save_path)}")
        print(f"    对手识别为: 【{opp_name}】")
    else:
        print(" -> ❌ 进入第二场详情页失败！")

    print(" -> 正在返回战报列表...")
    return_to_list(hwnd, box)
    time.sleep(0.8)

    # ---------------------------------------------------------
    # STEP 3: 展开并采集第一场（第一场: 黑风山成安）
    # ---------------------------------------------------------
    print("\n" + "=" * 50)
    print("▶ 步骤 3/3: 展开并采集【第一场】（黑风山成安）...")
    screen = capture_box(box)
    lines = ocr_lines(screen)

    row1_y = None
    for l in lines:
        t = l.text.replace(' ', '')
        if "第一场" in t and l.words:
            row1_y = int(l.words[0].bounding_rect.y + l.words[0].bounding_rect.height / 2)
            break

    if not row1_y:
        print("[WARN] 未在画面中找到【第一场】条目，使用基准坐标 769...")
        row1_y = 769

    print(f" -> 点击【第一场】折叠条 (X={int(W*0.35)}, Y={row1_y}) 展开手风琴...")
    real_click(box["left"] + int(W * 0.35), box["top"] + row1_y, hold_sec=0.15)
    time.sleep(1.2)  # Wait for accordion expansion animation

    screen_exp1 = capture_box(box)
    lines_exp1 = ocr_lines(screen_exp1)
    date1_y = None
    date1_str = "Round1"
    for l in lines_exp1:
        t = l.text.replace(' ', '')
        m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', t)
        if m and l.words:
            yr, mon, d, hr, mi, sec = m.groups()
            date1_str = f"20{yr}-{int(mon):02d}-{int(d):02d}_{int(hr):02d}{int(mi):02d}{int(sec):02d}"
            date1_y = int(l.words[0].bounding_rect.y + l.words[0].bounding_rect.height / 2)
            break

    if date1_y:
        click1_y = box["top"] + max(int(0.18 * H), date1_y - int(0.12 * H))
    else:
        click1_y = box["top"] + int(0.52 * H)

    click1_x = box["left"] + int(W * 0.50)
    print(f" -> 瞄准第一场大卡片中央【平】字 (坐标: {click1_x}, {click1_y})，准备进入全屏详情...")
    detail_3 = enter_detail_with_retry(box, click1_x, click1_y)
    if detail_3:
        opp_name = extract_opponent_from_detail(detail_3)
        save_path = os.path.join(out_dir, f"3_第一场_平_{opp_name}_{date1_str}.png")
        detail_3.save(save_path)
        print(f" -> ✅ 成功保存第 3 张战报: {os.path.basename(save_path)}")
        print(f"    对手识别为: 【{opp_name}】")
    else:
        print(" -> ❌ 进入第一场详情页失败！")

    print(" -> 正在返回战报列表...")
    return_to_list(hwnd, box)
    time.sleep(0.5)

    print("\n" + "=" * 60)
    print("🎉 攻坚任务已执行完毕！请检查目录下的 3 张高清全屏战报截图：")
    print(f"📂 目录: {out_dir}")
    print("=" * 60)
    input("\n测试完毕，按【回车键】关闭本窗口...")

if __name__ == "__main__":
    main()
