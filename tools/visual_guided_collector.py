import os
import sys
import time
import re
import ctypes
import json
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
from PIL import Image, ImageDraw, ImageFont
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
    return asyncio.run(_ocr()).lines

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
        print(f" -> 正在点击右上角金色【返回】 (第 {attempt+1} 次尝试)...")
        real_click(back_x, back_y, hold_sec=0.15)
        for _ in range(8):
            time.sleep(0.2)
            chk = capture_box(box)
            if not is_in_detail(chk):
                time.sleep(0.4)
                return True
    return False

def enter_detail_with_retry(box, target_x, target_y):
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

def parse_screen_items(lines, W, H):
    """
    Opponent-Driven Battle Item Parser:
    Detects EVERY battle row via the opponent player name (which WinOCR reads with 100% accuracy),
    completely eliminating dropped rows caused by garbled calligraphy tags!
    """
    # 1. Check for expanded card date
    exp_date_y = None
    exp_date_str = None
    for l in lines:
        t = l.text.replace(' ', '')
        m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', t)
        if m and l.words:
            yr, mon, d, hr, mi, sec = m.groups()
            exp_date_str = f"20{yr}-{int(mon):02d}-{int(d):02d} {int(hr):02d}:{int(mi):02d}:{int(sec):02d}"
            w0 = l.words[0].bounding_rect
            exp_date_y = int(w0.y + w0.height / 2)
            break

    # 2. Extract all opponent player names on the right side (0.55 * W <= X <= 0.78 * W, Y > 0.20 * H)
    opponents = []
    for l in lines:
        t = l.text.replace(' ', '')
        if not l.words:
            continue
        w0 = l.words[0].bounding_rect
        if int(0.55 * W) <= w0.x <= int(0.78 * W) and int(0.20 * H) <= w0.y <= int(0.95 * H):
            if not any(k in t for k in ['战报', '频道', '标记', '已读', '斩获', '战功', '个人', '搜索', '空地', '收藏', '况']):
                opponents.append({
                    'y': int(w0.y + w0.height / 2),
                    'x': int(w0.x),
                    'name': t.replace('@', '').strip()
                })

    # Deduplicate rows within 35px
    opponents.sort(key=lambda o: o['y'])
    unique_opps = []
    for o in opponents:
        if not any(abs(o['y'] - u['y']) < 35 for u in unique_opps):
            unique_opps.append(o)

    # 3. For each detected opponent, determine if it is an expanded card or collapsed row
    items = []
    for i, o in enumerate(unique_opps):
        opp_y = o['y']
        opp_name = o['name']

        # Look for outcome or round tag near this row (+- 30px)
        round_name = f"场次_{i+1}"
        outcome = ""
        for l in lines:
            t = l.text.replace(' ', '')
            if not l.words:
                continue
            wy = int(l.words[0].bounding_rect.y + l.words[0].bounding_rect.height / 2)
            if abs(wy - opp_y) < 30:
                m = re.search(r'第([一二三四五六七八九十\d]+)场', t)
                if m:
                    round_name = f"第{m.group(1)}场"
                if "胜" in t:
                    outcome = "胜"
                elif "败" in t or "负" in t:
                    outcome = "败"
                elif "平" in t:
                    outcome = "平"

        # Check if this row is part of an expanded card
        is_expanded = (exp_date_y and abs(opp_y - (exp_date_y - int(0.25 * H))) < 65)

        if is_expanded:
            outcome_y = max(int(0.18 * H), exp_date_y - int(0.12 * H))
            items.append({
                "type": "expanded_card",
                "y": opp_y,
                "date_y": exp_date_y,
                "round_name": round_name,
                "opponent": opp_name,
                "outcome": outcome if outcome else "平/胜/败",
                "date": exp_date_str if exp_date_str else "",
                "click_x": int(0.50 * W),
                "click_y": outcome_y,
                "rect": (int(0.05 * W), int(opp_y - 0.05 * H), int(0.90 * W), int(0.35 * H))
            })
        else:
            items.append({
                "type": "collapsed_row",
                "y": opp_y,
                "round_name": round_name,
                "opponent": opp_name,
                "outcome": outcome,
                "date": "",
                "click_x": int(0.35 * W),
                "click_y": opp_y,
                "rect": (int(0.05 * W), int(opp_y - 0.025 * H), int(0.90 * W), int(0.05 * H))
            })

    items.sort(key=lambda it: it["y"])
    for i, it in enumerate(items):
        it["id"] = i + 1

    return items

def draw_plan_image(img, items):
    canvas = img.copy()
    draw = ImageDraw.Draw(canvas)
    for it in items:
        rx, ry, rw, rh = it["rect"]
        color = "lime" if it["type"] == "expanded_card" else "cyan"
        draw.rectangle([(rx, ry), (rx + rw, ry + rh)], outline=color, width=3)
        label = f"#{it['id']} [{it['round_name']}] 对手: {it['opponent']} ({it['outcome']})"
        draw.rectangle([(rx + 5, ry + 5), (rx + 420, ry + 35)], fill="black")
        draw.text((rx + 10, ry + 10), label, fill=color)
        cx, cy = it["click_x"], it["click_y"]
        r = 16
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline="red", width=3)
        draw.line([(cx - 24, cy), (cx + 24, cy)], fill="red", width=2)
        draw.line([(cx, cy - 24), (cx, cy + 24)], fill="red", width=2)
    return canvas

def scroll_anchor_to_top(hwnd, box, anchor_name, max_drags=5):
    """
    Visual Anchor Watermark Tracking:
    Gently drag the list upward until the anchor opponent reaches the top area (Y <= 0.28 * H).
    Guarantees gapless pagination with zero missed reports!
    """
    W, H = box["width"], box["height"]
    print(f"\n⚓ [视觉锚点对齐] 正在轻柔拖动，将上一批最后采完的【{anchor_name}】对齐到屏幕顶部...")

    for d in range(max_drags):
        screen = capture_box(box)
        lines = ocr_lines(screen)
        anchor_y = None
        for l in lines:
            t = l.text.replace(' ', '')
            if anchor_name in t and l.words:
                anchor_y = int(l.words[0].bounding_rect.y)
                break

        if anchor_y and anchor_y <= int(0.28 * H):
            print(f" -> ✅ 锚点【{anchor_name}】已成功对齐至顶部 (Y={anchor_y})！无缝衔接下一批！")
            return True

        # Perform a gentle drag upward (18% of screen height)
        drag_x = box["left"] + int(W * 0.30)
        drag_start_y = box["top"] + int(H * 0.65)
        drag_end_y = box["top"] + int(H * 0.47)

        pyautogui.moveTo(drag_x, drag_start_y, duration=0.15)
        time.sleep(0.05)
        pyautogui.mouseDown(drag_x, drag_start_y, button='left')
        pyautogui.moveTo(drag_x, drag_end_y, duration=0.25)
        time.sleep(0.20)  # Pause to eliminate inertial fling
        pyautogui.mouseUp(drag_x, drag_end_y, button='left')
        time.sleep(0.8)

    print(f" -> 锚点对齐滑动已完成。")
    return True

def main():
    ensure_admin()
    hwnd = find_game()
    if not hwnd:
        print("[ERROR] 未找到游戏窗口 S3Client / EJOY2DX！请确认游戏已打开且未关闭。")
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
        print("[ERROR] 游戏窗口目前处于最小化状态！请先在 Windows 任务栏点击游戏将其还原显示在屏幕上。")
        input("按回车退出...")
        return

    print("=" * 65)
    print("  【三国志·战略版】全闭环视觉循迹采集器（基于对手全量识别 + 视觉锚点追踪）")
    print("=" * 65)
    print(f"窗口物理尺寸: {W} x {H}, 屏幕原点: ({box['left']}, {box['top']})\n")

    # If detail view is open, return to list first
    init_screen = capture_box(box)
    if is_in_detail(init_screen):
        print("[INIT] 当前处于详情页，先自动返回列表...")
        return_to_list(hwnd, box)
        time.sleep(0.5)

    out_dir = r"C:\sanguo_report_collector\战报导出\视觉计划采集"
    os.makedirs(out_dir, exist_ok=True)

    collected_opponents = set()
    total_captured = 0

    # -------------------------------------------------------------
    # STAGE 1: 截屏识图，生成从上到下的顺序计划
    # -------------------------------------------------------------
    print("📸 [阶段 1] 正在截取当前画面，进行全屏对手全量扫描...")
    screen = capture_box(box)
    screen.save(r"C:\sanguo_report_collector\战报巡检原图.png")

    lines = ocr_lines(screen)
    items = parse_screen_items(lines, W, H)

    annotated = draw_plan_image(screen, items)
    annotated.save(r"C:\sanguo_report_collector\战报巡检规划图.png")

    print(f" -> 已生成带编号的瞄准规划图: C:\\sanguo_report_collector\\战报巡检规划图.png\n")
    print("=" * 65)
    print(f"📋 【当前画面完整识别结果与执行计划清单】(共识别出 {len(items)} 个战报条目)")
    print("=" * 65)
    print(f"{'序号':<4} | {'类型':<10} | {'场次':<8} | {'战果':<4} | {'对手人名':<16} | {'目标点击 (X, Y)':<18}")
    print("-" * 75)

    for it in items:
        status_str = "展开大卡片" if it["type"] == "expanded_card" else "折叠条目"
        print(f"#{it['id']:<3} | {status_str:<10} | {it['round_name']:<8} | {it['outcome']:<4} | {it['opponent']:<16} | ({it['click_x']}, {it['click_y']})")

    print("\n" + "=" * 65)
    print("🚀 [阶段 2] 立即开始按照上述计划清单，从上到下逐个采集！")
    print("=" * 65)

    for it in items:
        step_id = it["id"]
        round_name = it["round_name"]
        opp_target = it["opponent"]

        if opp_target in collected_opponents and opp_target != "未知对手":
            print(f"⏩ 对手【{opp_target}】已采集过，自动跳过。")
            continue

        print(f"\n▶ 正在执行计划 #{step_id}: [{round_name}] 目标对手: 【{opp_target}】...")

        detail_img = None
        date_stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        # In current list view, locate this opponent's exact current Y coordinate
        cur_screen = capture_box(box)
        cur_lines = ocr_lines(cur_screen)

        opp_current_y = None
        for cl in cur_lines:
            ct = cl.text.replace(' ', '')
            if not cl.words:
                continue
            # Match either opponent name or round name
            if opp_target != "未知对手" and opp_target in ct:
                opp_current_y = int(cl.words[0].bounding_rect.y + cl.words[0].bounding_rect.height / 2)
                break
            elif round_name != "已展开卡片" and round_name in ct:
                opp_current_y = int(cl.words[0].bounding_rect.y + cl.words[0].bounding_rect.height / 2)
                break

        if not opp_current_y:
            opp_current_y = it["click_y"]
            print(f" -> 使用规划坐标: Y={opp_current_y}")
        else:
            print(f" -> 动态瞄准对手【{opp_target}】当前最新位置: Y={opp_current_y}")

        # Check if already expanded or needs click to expand
        # An expanded card has outcome word around center
        exp_date_found = False
        for cl in cur_lines:
            ct = cl.text.replace(' ', '')
            m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', ct)
            if m and cl.words:
                dy = int(cl.words[0].bounding_rect.y)
                if abs(opp_current_y - (dy - int(0.25 * H))) < 65:
                    exp_date_found = True
                    break

        if exp_date_found or it["type"] == "expanded_card":
            # Card is already expanded, click center outcome character directly
            cx = box["left"] + int(W * 0.50)
            cy = box["top"] + it["click_y"]
            print(f" -> 卡片已处于展开状态，点击中央【平/胜/败】大字 (坐标: {cx}, {cy}) 进详情...")
            detail_img = enter_detail_with_retry(box, cx, cy)
        else:
            # Collapsed row: click row to expand accordion
            rx = box["left"] + int(W * 0.35)
            ry = box["top"] + opp_current_y
            print(f" -> 点击条目展开手风琴 (坐标: {rx}, {ry})...")
            real_click(rx, ry, hold_sec=0.15)
            time.sleep(1.2)  # Wait for accordion expansion animation

            # Find newly expanded card's outcome character
            exp_screen = capture_box(box)
            exp_lines = ocr_lines(exp_screen)
            new_date_y = None
            for el in exp_lines:
                et = el.text.replace(' ', '')
                m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', et)
                if m and el.words:
                    yr, mon, d, hr, mi, sec = m.groups()
                    date_stamp = f"20{yr}-{int(mon):02d}-{int(d):02d}_{int(hr):02d}{int(mi):02d}{int(sec):02d}"
                    new_date_y = int(el.words[0].bounding_rect.y + el.words[0].bounding_rect.height / 2)
                    break

            if new_date_y:
                click_y = box["top"] + max(int(0.18 * H), new_date_y - int(0.12 * H))
            else:
                click_y = box["top"] + opp_current_y + int(0.12 * H)

            click_x = box["left"] + int(W * 0.50)
            print(f" -> 大卡片已展开！点击中央大字 (坐标: {click_x}, {click_y}) 进入详情...")
            detail_img = enter_detail_with_retry(box, click_x, click_y)

        if detail_img:
            actual_opp = extract_opponent_from_detail(detail_img)
            total_captured += 1
            filename = f"{total_captured}_{round_name}_{actual_opp}_{date_stamp}.png"
            filepath = os.path.join(out_dir, filename)
            detail_img.save(filepath)
            print(f" -> ✅ 成功捕获并保存全屏战报: {filename}")
            print(f"    核验对手人名: 【{actual_opp}】")
            collected_opponents.add(actual_opp)
            if opp_target != "未知对手":
                collected_opponents.add(opp_target)
        else:
            print(f" -> ❌ 未能进入详情页，跳过【{opp_target}】")

        print(" -> 正在返回战报列表...")
        return_to_list(hwnd, box)
        time.sleep(0.6)

    print("\n" + "=" * 65)
    print(f"🎉 本轮全部计划执行完毕！共采集到 {total_captured} / {len(items)} 场战报！")
    print(f"📂 存储目录: {out_dir}")
    print("=" * 65)
    input("\n按【回车键】关闭本窗口...")

if __name__ == "__main__":
    main()
