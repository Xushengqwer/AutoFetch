import os
import sys
import time
import re
import ctypes
from datetime import datetime

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
    pyautogui.moveTo(x, y, duration=0.12)
    time.sleep(0.04)
    pyautogui.mouseDown(x, y, button='left')
    time.sleep(hold_sec)
    pyautogui.mouseUp(x, y, button='left')
    time.sleep(0.10)

def ocr_lines(pil_img):
    async def _ocr():
        return await winocr.recognize_pil(pil_img, lang="zh-Hans")
    return asyncio.run(_ocr()).lines

def is_in_report_list(lines):
    joined = "".join([l.text.replace(' ', '') for l in lines])
    return any(k in joined for k in ["个人战报", "全部标记已读", "只看个人", "交战", "斩获战功", "鏖战"])

def is_in_detail(lines):
    joined = "".join([l.text.replace(' ', '') for l in lines])
    return ("战斗回放" in joined or "回放" in joined or "承天靖世" in joined)

def ensure_in_report_list(hwnd, box):
    """Guarantees the game is in the battle report list page; recovers from world map or detail page."""
    W, H = box["width"], box["height"]
    for attempt in range(4):
        screen = capture_box(box)
        lines = ocr_lines(screen)

        if is_in_report_list(lines):
            return True

        if is_in_detail(lines):
            print(" -> 当前在战斗回放详情页，点击右上角【返回】...")
            back_x = box["left"] + int(W * 0.945)
            back_y = box["top"] + int(H * 0.038)
            real_click(back_x, back_y, hold_sec=0.15)
            time.sleep(0.8)
            continue

        joined = "".join([l.text.replace(' ', '') for l in lines])
        if any(k in joined for k in ["武将", "战法", "政厅", "招募", "征兵", "地图", "势力"]):
            print(" -> 检测到处于大地图主界面，正在自动点击左下角【战报】切换...")
            # Click bottom-left 【战报】 icon
            real_click(box["left"] + int(W * 0.055), box["top"] + int(H * 0.815), hold_sec=0.15)
            time.sleep(1.2)
            # Click 【交战】 tab
            real_click(box["left"] + int(W * 0.265), box["top"] + int(H * 0.160), hold_sec=0.15)
            time.sleep(0.8)
            continue

        time.sleep(0.5)

    return False

def return_to_list_verified(hwnd, box):
    W, H = box["width"], box["height"]
    back_x = box["left"] + int(W * 0.945)
    back_y = box["top"] + int(H * 0.038)

    for attempt in range(5):
        screen = capture_box(box)
        lines = ocr_lines(screen)
        if not is_in_detail(lines):
            return True
        print(f" -> 点击右上角金色【返回】退出详情 (第 {attempt+1} 次)...")
        real_click(back_x, back_y, hold_sec=0.15)
        for _ in range(8):
            time.sleep(0.2)
            chk = capture_box(box)
            chk_lines = ocr_lines(chk)
            if not is_in_detail(chk_lines):
                time.sleep(0.3)
                return True
    return False

def sanitize_filename(name):
    return re.sub(r'[\\/*?:\x22<>|@\s]', '_', name)

def extract_opponent_from_detail(screen_pil):
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

def find_opponents_on_screen(screen_pil, W, H):
    """
    Scans the live screen and returns all detected battle opponents strictly sorted from TOP to BOTTOM (Y ascending).
    Returns list of dicts:
    [
        {'name': '龍漢钧宸', 'y': 405, 'x': 1215},
        {'name': '龍漢丨鲲鵬', 'y': 480, 'x': 1215},
        {'name': '黑风山成安', 'y': 556, 'x': 1215},
        {'name': '九指|骨佬', 'y': 686, 'x': 1177}
    ]
    """
    lines = ocr_lines(screen_pil)
    opponents = []

    for l in lines:
        t = l.text.replace(' ', '')
        if not l.words:
            continue
        w0 = l.words[0].bounding_rect
        # Opponent name column is strictly at 55% to 78% of width, Y between 20% and 94% of height
        if int(0.55 * W) <= w0.x <= int(0.78 * W) and int(0.20 * H) <= w0.y <= int(0.94 * H):
            if not any(k in t for k in ['战报', '频道', '标记', '已读', '斩获', '战功', '个人', '搜索', '空地', '收藏', '况', '地图']):
                clean_name = t.replace('@', '').strip()
                if len(clean_name) >= 2:
                    opponents.append({
                        'y': int(w0.y + w0.height / 2),
                        'x': int(w0.x),
                        'name': clean_name
                    })

    # Sort strictly from top to bottom
    opponents.sort(key=lambda o: o['y'])

    # Deduplicate rows within 35px
    unique_opps = []
    for o in opponents:
        if not any(abs(o['y'] - u['y']) < 35 for u in unique_opps):
            unique_opps.append(o)

    return unique_opps

def is_row_expanded(lines, row_y, H):
    """Checks if the battle row at row_y is currently expanded (has outcome word & date below it)."""
    for l in lines:
        t = l.text.replace(' ', '')
        m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', t)
        if m and l.words:
            dy = int(l.words[0].bounding_rect.y)
            # The date of an expanded card is located 20% to 35% H below the opponent row
            if int(0.15 * H) < (dy - row_y) < int(0.38 * H):
                return True, dy
    return False, None

def scroll_anchor_to_top(hwnd, box, anchor_name, max_drags=5):
    """
    Visual Anchor Watermark Tracking:
    Gently drags upward until anchor_name reaches near the top of the viewport (Y <= 0.28 * H).
    """
    W, H = box["width"], box["height"]
    print(f"\n⚓ [视觉锚点滑动] 正在轻柔上滑，将已采完的【{anchor_name}】推至顶部对齐...")

    for d in range(max_drags):
        screen = capture_box(box)
        opps = find_opponents_on_screen(screen, W, H)
        anchor_y = None
        for o in opps:
            if anchor_name in o['name'] or o['name'] in anchor_name:
                anchor_y = o['y']
                break

        if anchor_y is None:
            # Anchor scrolled past the top
            print(f" -> 锚点已滚动出顶端，对齐完成。")
            return True

        if anchor_y <= int(0.28 * H):
            print(f" -> ✅ 锚点【{anchor_name}】已成功对齐到顶部 (Y={anchor_y})！无缝衔接下一批！")
            return True

        # Perform a gentle drag upward (18% of screen height)
        drag_x = box["left"] + int(W * 0.30)
        drag_start_y = box["top"] + int(H * 0.65)
        drag_end_y = box["top"] + int(H * 0.47)

        pyautogui.moveTo(drag_x, drag_start_y, duration=0.15)
        time.sleep(0.05)
        pyautogui.mouseDown(drag_x, drag_start_y, button='left')
        pyautogui.moveTo(drag_x, drag_end_y, duration=0.25)
        time.sleep(0.20)  # Stop inertia fling
        pyautogui.mouseUp(drag_x, drag_end_y, button='left')
        time.sleep(0.8)

    return True

def main():
    ensure_admin()
    hwnd = find_game()
    if not hwnd:
        print("[ERROR] 未找到游戏窗口 S3Client / EJOY2DX！请确认已打开 PC 客户端。")
        input("按回车退出...")
        return

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.6)

    box = get_box(hwnd)
    W, H = box["width"], box["height"]

    if W <= 100 or H <= 100:
        print("[ERROR] 游戏窗口处于最小化状态！请在任务栏点击游戏将其还原显示在屏幕上。")
        input("按回车退出...")
        return

    print("=" * 65)
    print("  【三国志·战略版】自顶向下 视觉闭环循迹采集器（终极防漏防重版）")
    print("=" * 65)
    print(f"物理窗口尺寸: {W} x {H}, 原点: ({box['left']}, {box['top']})\n")

    out_dir = r"C:\sanguo_report_collector\战报导出\视觉计划采集"
    os.makedirs(out_dir, exist_ok=True)

    collected_names = set()
    total_saved = 0

    # Ensure in battle reports list
    print("🔍 [检查界面] 正在校验当前是否在【战报列表】界面...")
    if not ensure_in_report_list(hwnd, box):
        print("[ERROR] 无法自动切回战报列表！请手动在游戏主界面点击【战报】->【交战】。")
        input("按回车退出...")
        return

    print("✅ 已确认处于战报列表界面！开始自顶向下采集！\n")

    page_idx = 1
    max_pages = 20

    while page_idx <= max_pages:
        print(f"\n=======================================================")
        print(f"📄 【第 {page_idx} 屏】正在截屏识图，从上到下扫描所有对手...")
        print(f"=======================================================")

        screen = capture_box(box)
        opponents = find_opponents_on_screen(screen, W, H)

        if not opponents:
            print("⚠️ 当前画面未检测到任何对手，尝试轻微拖动刷新...")
            scroll_anchor_to_top(hwnd, box, "dummy", max_drags=1)
            time.sleep(0.5)
            page_idx += 1
            continue

        print(f"📋 当前画面从上到下共发现 {len(opponents)} 个战报条目：")
        for i, o in enumerate(opponents):
            tag = "✅ 已采" if o['name'] in collected_names else "⏳ 待采"
            print(f"  #{i+1}: Y={o['y']} | 对手: 【{o['name']}】 ({tag})")

        # Find uncollected opponents in strict top-to-bottom order
        new_opps = [o for o in opponents if o['name'] not in collected_names]

        if not new_opps:
            print(f"\n📜 当前屏幕上的 {len(opponents)} 个对手已全部采集过！准备以最后一个对手为锚点上滑...")
            last_anchor = opponents[-1]['name']
            scroll_anchor_to_top(hwnd, box, last_anchor)
            page_idx += 1
            continue

        # Process each uncollected opponent one by one from TOP to BOTTOM
        for target in new_opps:
            target_name = target['name']
            if target_name in collected_names:
                continue

            print(f"\n▶ [自顶向下] 正在处理目标对手: 【{target_name}】...")

            # STEP A: Fresh live screenshot to locate target_name in current frame
            cur_screen = capture_box(box)
            cur_opps = find_opponents_on_screen(cur_screen, W, H)
            cur_lines = ocr_lines(cur_screen)

            target_pos = None
            for co in cur_opps:
                if co['name'] == target_name or target_name in co['name']:
                    target_pos = co
                    break

            if not target_pos:
                print(f"⚠️ 在当前画面未找到【{target_name}】，可能受展开位移影响，继续下一项。")
                continue

            row_y = target_pos['y']
            print(f" -> 实时锁定【{target_name}】当前坐标: Y={row_y}")

            # STEP B: Check if already expanded or needs expanding
            is_exp, date_y = is_row_expanded(cur_lines, row_y, H)

            if is_exp and date_y:
                print(f" -> 该卡片已处于展开状态！点击中央【平/胜/败】大字进入详情...")
                cx = box["left"] + int(W * 0.50)
                cy = box["top"] + max(int(0.18 * H), date_y - int(0.12 * H))
                real_click(cx, cy, hold_sec=0.15)
            else:
                print(f" -> 条目处于折叠状态，点击行 (X={int(W*0.35)}, Y={row_y}) 展开手风琴...")
                real_click(box["left"] + int(W * 0.35), box["top"] + row_y, hold_sec=0.15)
                time.sleep(1.2)  # Wait for accordion expansion animation

                # STEP C: Fresh live screenshot after expansion to find outcome character
                exp_screen = capture_box(box)
                exp_lines = ocr_lines(exp_screen)
                exp_opps = find_opponents_on_screen(exp_screen, W, H)

                # Locate the newly expanded card's center
                new_date_y = None
                for el in exp_lines:
                    t = el.text.replace(' ', '')
                    m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', t)
                    if m and el.words:
                        new_date_y = int(el.words[0].bounding_rect.y)
                        break

                if new_date_y:
                    outcome_click_y = box["top"] + max(int(0.18 * H), new_date_y - int(0.12 * H))
                else:
                    outcome_click_y = box["top"] + row_y + int(0.14 * H)

                cx = box["left"] + int(W * 0.50)
                print(f" -> 手风琴已展开！瞄准中央大字 (X={cx}, Y={outcome_click_y}) 进入详情...")
                real_click(cx, outcome_click_y, hold_sec=0.15)

            # STEP D: Verify Detail View entered
            in_detail = False
            detail_img = None
            for _ in range(10):
                time.sleep(0.3)
                chk_screen = capture_box(box)
                chk_lines = ocr_lines(chk_screen)
                if is_in_detail(chk_lines):
                    in_detail = True
                    detail_img = chk_screen
                    break

            if in_detail:
                actual_opp = extract_opponent_from_detail(detail_img)
                print(f" -> 详情页加载成功！实测核验对手人名: 【{actual_opp}】")

                # ANTI-DUPLICATION CHECK
                if actual_opp in collected_names:
                    print(f" -> ⏩ 检测到该战报【{actual_opp}】先前已收录，跳过重复保存。")
                else:
                    total_saved += 1
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                    filename = f"{total_saved}_{actual_opp}_{timestamp}.png"
                    filepath = os.path.join(out_dir, filename)
                    detail_img.save(filepath)
                    print(f" -> ✅ 成功保存第 {total_saved} 张战报: {filename}")
                    collected_names.add(actual_opp)
                    collected_names.add(target_name)

                # STEP E: Return to list
                return_to_list_verified(hwnd, box)
                time.sleep(0.6)
            else:
                print(f" -> ⚠️ 点击后未进入详情页，重试或继续下一项。")
                ensure_in_report_list(hwnd, box)

        # After finishing all new items in this view, anchor scroll to next view
        last_anchor = opponents[-1]['name']
        scroll_anchor_to_top(hwnd, box, last_anchor)
        page_idx += 1

    print("\n" + "=" * 65)
    print(f"🎉 全部采集流程圆满完成！共捕获 {total_saved} 张无重复、无遗漏高清战报！")
    print(f"📂 存储目录: {out_dir}")
    print("=" * 65)
    input("\n按【回车键】关闭本窗口...")

if __name__ == "__main__":
    main()
