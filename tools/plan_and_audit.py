import os
import sys
import time
import re
import ctypes
import json

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

# DPI Awareness
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

def capture_box(box):
    with mss.MSS() as sct:
        sct_img = sct.grab(box)
        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

def ocr_lines(pil_img):
    async def _ocr():
        return await winocr.recognize_pil(pil_img, lang="zh-Hans")
    return asyncio.run(_ocr()).lines

def parse_screen_items(lines, W, H):
    """
    Parses OCR lines into structured battle items sorted strictly from top to bottom.
    Returns: list of dicts:
    [
        {
            "id": 1,
            "type": "expanded_card",
            "y": 336,
            "round_name": "第三场",
            "opponent": "龍漢丨钧宸",
            "outcome": "平",
            "date": "2026-10-24 16:24:29",
            "click_type": "outcome_word",
            "box": [x, y, w, h]
        },
        ...
    ]
    """
    items = []

    # 1. Look for expanded cards (has date string)
    for l in lines:
        t = l.text.replace(' ', '')
        m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', t)
        if m and l.words:
            yr, mon, d, hr, mi, sec = m.groups()
            date_str = f"20{yr}-{int(mon):02d}-{int(d):02d} {int(hr):02d}:{int(mi):02d}:{int(sec):02d}"
            w0 = l.words[0].bounding_rect
            date_y = int(w0.y + w0.height / 2)

            # Find opponent name for this expanded card (player name is between 55% and 78% W, around Y=250-450)
            opp_name = "未知对手"
            card_top_y = max(int(0.15 * H), date_y - int(0.35 * H))
            for l2 in lines:
                t2 = l2.text.replace(' ', '')
                if not l2.words:
                    continue
                w2 = l2.words[0].bounding_rect
                if int(0.55 * W) <= w2.x <= int(0.78 * W) and card_top_y <= w2.y < date_y - int(0.15 * H):
                    if not any(k in t2 for k in ['战报', '频道', '标记', '已读', '斩获', '战功', '个人', '搜索', '空地']):
                        opp_name = t2.replace('@', '').strip()
                        break

            # Central outcome character position
            outcome_y = max(int(0.18 * H), date_y - int(0.12 * H))
            items.append({
                "type": "expanded_card",
                "y": card_top_y,
                "date_y": date_y,
                "round_name": "已展开卡片",
                "opponent": opp_name,
                "outcome": "平/胜/败",
                "date": date_str,
                "click_x": int(0.50 * W),
                "click_y": outcome_y,
                "rect": (int(0.05 * W), card_top_y, int(0.90 * W), date_y - card_top_y + 30)
            })
            break

    # 2. Look for collapsed round rows: 【第X场 胜/平/败】
    for l in lines:
        t = l.text.replace(' ', '')
        m = re.search(r'[【〔\[(]?第([一二三四五六七八九十\d]+)场\s*(胜|败|平)?[】〕\])]?', t)
        if m and l.words:
            round_str = f"第{m.group(1)}场"
            outcome = m.group(2) if m.group(2) else ""
            w0 = l.words[0].bounding_rect
            row_y = int(w0.y + w0.height / 2)

            if row_y < int(0.18 * H) or row_y > int(0.95 * H):
                continue

            # Find opponent name on the right side of this same row (player name between 55% and 78% W)
            opp_name = "未知对手"
            for l2 in lines:
                t2 = l2.text.replace(' ', '')
                if not l2.words:
                    continue
                w2 = l2.words[0].bounding_rect
                if int(0.55 * W) <= w2.x <= int(0.78 * W) and abs(w2.y + w2.height/2 - row_y) < int(0.035 * H):
                    if not any(k in t2 for k in ['收藏', '王', '战报', '空地']):
                        opp_name = t2.replace('@', '').strip()
                        break

            items.append({
                "type": "collapsed_row",
                "y": row_y,
                "round_name": round_str,
                "opponent": opp_name,
                "outcome": outcome,
                "date": "",
                "click_x": int(0.35 * W),
                "click_y": row_y,
                "rect": (int(0.05 * W), int(row_y - 0.025 * H), int(0.90 * W), int(0.05 * H))
            })

    # Sort strictly by Y ascending (from top to bottom)
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
        # Draw badge
        draw.rectangle([(rx + 5, ry + 5), (rx + 420, ry + 35)], fill="black")
        draw.text((rx + 10, ry + 10), label, fill=color)
        # Draw aim crosshair
        cx, cy = it["click_x"], it["click_y"]
        r = 16
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline="red", width=3)
        draw.line([(cx - 24, cy), (cx + 24, cy)], fill="red", width=2)
        draw.line([(cx, cy - 24), (cx, cy + 24)], fill="red", width=2)
    return canvas

def main():
    ensure_admin()
    hwnd = find_game()
    if not hwnd:
        print("[ERROR] 未找到游戏窗口 S3Client / EJOY2DX！")
        input("按回车退出...")
        return

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.5)

    crect = win32gui.GetClientRect(hwnd)
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    box = {"left": pt[0], "top": pt[1], "width": crect[2] - crect[0], "height": crect[3] - crect[1]}
    W, H = box["width"], box["height"]

    print("=" * 60)
    print("  【三国志·战略版】视觉巡检与采集执行计划生成器")
    print("=" * 60)
    print(f"窗口物理尺寸: {W} x {H}, 原点: ({box['left']}, {box['top']})\n")

    screen = capture_box(box)
    raw_path = r"C:\sanguo_report_collector\战报巡检原图.png"
    screen.save(raw_path)
    print(f"[1/3] 已捕获当前电脑游戏真实画面: {raw_path}")

    lines = ocr_lines(screen)
    items = parse_screen_items(lines, W, H)

    annotated = draw_plan_image(screen, items)
    annotated_path = r"C:\sanguo_report_collector\战报巡检规划图.png"
    annotated.save(annotated_path)
    print(f"[2/3] 已生成带序号与瞄准框的可视化规划图: {annotated_path}")

    print("\n" + "=" * 60)
    print(f"📋 【当前画面完整识别结果与执行计划清单】(共识别出 {len(items)} 个战报项目)")
    print("=" * 60)
    print(f"{'序号':<4} | {'类型':<10} | {'场次':<8} | {'结果':<4} | {'对手人名':<16} | {'目标点击点 (X, Y)':<18}")
    print("-" * 75)

    plan_data = []
    for it in items:
        status_str = "展开大卡片" if it["type"] == "expanded_card" else "折叠条目"
        print(f"#{it['id']:<3} | {status_str:<10} | {it['round_name']:<8} | {it['outcome']:<4} | {it['opponent']:<16} | ({it['click_x']}, {it['click_y']})")
        plan_data.append({
            "step": it["id"],
            "type": it["type"],
            "round": it["round_name"],
            "outcome": it["outcome"],
            "opponent": it["opponent"],
            "date": it["date"],
            "click_x": it["click_x"],
            "click_y": it["click_y"]
        })

    plan_json_path = r"C:\sanguo_report_collector\战报采集计划.json"
    with open(plan_json_path, 'w', encoding='utf-8') as f:
        json.dump(plan_data, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"✅ 执行计划已保存至: {plan_json_path}")
    print("现在可以依次按此清单执行绝对稳健的视觉闭环采集！")
    input("\n按【回车键】关闭本窗口...")

if __name__ == "__main__":
    main()
