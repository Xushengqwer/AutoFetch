import os
import sys
import time
import re
import ctypes
import shutil
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

import cv2
import numpy as np
import win32gui
import win32con
import win32service
import mss
from PIL import Image, ImageDraw
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

def ocr_pil(pil_img):
    async def _ocr():
        return await winocr.recognize_pil(pil_img, lang="zh-Hans")
    return asyncio.run(_ocr()).lines

def is_in_detail(screen_pil):
    # Crop top-left area where '战斗回放' and '战斗地点' live, plus full check fallback
    W, H = screen_pil.size
    top_crop = screen_pil.crop((0, 0, int(0.40 * W), int(0.12 * H)))
    big = top_crop.resize((top_crop.width * 2, top_crop.height * 2), Image.Resampling.BICUBIC)
    lines = ocr_pil(big)
    joined = "".join([l.text.replace(' ', '') for l in lines])
    if "战斗回放" in joined or "回放" in joined or "战斗地点" in joined:
        return True
    # Fallback full OCR check
    lines_full = ocr_pil(screen_pil)
    joined_full = "".join([l.text.replace(' ', '') for l in lines_full])
    return ("战斗回放" in joined_full or "显示战法详情" in joined_full)

def return_to_list(box):
    W, H = box["width"], box["height"]
    back_x = box["left"] + int(W * 0.945)
    back_y = box["top"] + int(H * 0.038)
    for attempt in range(5):
        cur = capture_box(box)
        if not is_in_detail(cur):
            return True
        print(f"    -> 点击右上角【返回】退出详情 (第 {attempt+1} 次)...")
        real_click(back_x, back_y, hold_sec=0.15)
        for _ in range(8):
            time.sleep(0.2)
            chk = capture_box(box)
            if not is_in_detail(chk):
                time.sleep(0.4)
                return True
    return False

def clean_cjk(text):
    """Extract only Chinese/alphanumeric characters for reliable name matching."""
    return "".join(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]', text))

def same_opponent(name_a, name_b):
    ca = clean_cjk(name_a)
    cb = clean_cjk(name_b)
    if not ca or not cb:
        return False
    if ca == cb or ca in cb or cb in ca:
        return True
    # Compare last 2 characters (immune to left-side '[演]' icon OCR noise)
    if len(ca) >= 2 and len(cb) >= 2 and ca[-2:] == cb[-2:]:
        return True
    return False

def scan_battle_rows(screen_pil, tmpl_bgr):
    """
    100% Computer-Vision + Single-Row Cropped OCR Scanner:
    1. Uses OpenCV template matching on the friendly player badge ('哆丨绮罗生') to find exact Y of every battle row.
    2. Checks the left border pixel color (X=0.040*W..0.055*W): Blue ('我方', B-R > 35) = EXPANDED, Brown = COLLAPSED.
    3. Crops ONLY that row's opponent name box with BORDER_REPLICATE + 2x upscale to read the exact opponent name.
    """
    arr = np.array(screen_pil)
    img_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    W, H = screen_pil.size

    # Scale template if window is not 1920x1080
    scale_x = W / 1920.0
    scale_y = H / 1080.0
    if abs(scale_x - 1.0) > 0.02 or abs(scale_y - 1.0) > 0.02:
        t_h, t_w = tmpl_bgr.shape[:2]
        tmpl_scaled = cv2.resize(tmpl_bgr, (int(t_w * scale_x), int(t_h * scale_y)))
    else:
        tmpl_scaled = tmpl_bgr

    y_start, y_end = int(0.16 * H), int(0.96 * H)
    x_start, x_end = int(0.26 * W), int(0.38 * W)
    roi = img_bgr[y_start:y_end, x_start:x_end]
    res = cv2.matchTemplate(roi, tmpl_scaled, cv2.TM_CCOEFF_NORMED)

    min_gap = int(0.038 * H)
    ys = []
    for y in range(res.shape[0]):
        score = float(res[y, :].max())
        if score > 0.72:
            real_y = y_start + y + tmpl_scaled.shape[0] // 2
            if not ys or (real_y - ys[-1][0]) > min_gap:
                ys.append([real_y, score])
            elif score > ys[-1][1]:
                ys[-1] = [real_y, score]

    rows = []
    half_h = max(16, int(0.021 * H))
    for idx, (ry, sc) in enumerate(ys):
        # 1. Check Blue '我方' banner pixels below row header (X=0.040*W..0.055*W, Y=ry+20..ry+100)
        by1 = min(H - 1, ry + int(0.02 * H))
        by2 = min(H - 1, ry + int(0.09 * H))
        bx1, bx2 = int(0.040 * W), int(0.055 * W)
        patch = arr[by1:by2, bx1:bx2]
        mean_rgb = patch.mean(axis=(0, 1)) if patch.size > 0 else [0, 0, 0]
        bmr = float(mean_rgb[2]) - float(mean_rgb[0])
        is_expanded = bmr > 35.0

        # 2. Crop opponent name region for this specific row
        ox1, ox2 = int(0.59 * W), int(0.78 * W)
        oy1, oy2 = max(0, ry - half_h), min(H, ry + half_h)
        crop = arr[oy1:oy2, ox1:ox2]
        rep = cv2.copyMakeBorder(crop, 35, 35, 20, 20, cv2.BORDER_REPLICATE)
        pil_rep = Image.fromarray(rep)
        big = pil_rep.resize((pil_rep.width * 2, pil_rep.height * 2), Image.Resampling.BICUBIC)
        ores = ocr_pil(big)
        opp_raw = "".join([l.text.replace(' ', '') for l in ores])
        opp_clean = re.sub(r'^[0O@演`廊一一]+', '', opp_raw).strip()
        if not opp_clean:
            opp_clean = f"未知对手_{idx+1}"

        rows.append({
            "index": idx + 1,
            "y": ry,
            "score": sc,
            "expanded": is_expanded,
            "bmr": bmr,
            "opponent": opp_clean,
            "expand_click": (int(0.35 * W), ry),
            "detail_click": (int(0.50 * W), ry + int(0.14 * H))
        })

    return rows

def draw_debug_plan(screen_pil, rows, save_path):
    canvas = screen_pil.copy()
    draw = ImageDraw.Draw(canvas)
    W, H = canvas.size
    for r in rows:
        ry = r["y"]
        color = "lime" if r["expanded"] else "cyan"
        box_h = int(0.30 * H) if r["expanded"] else int(0.045 * H)
        top_y = ry - int(0.022 * H)
        draw.rectangle([(int(0.04 * W), top_y), (int(0.96 * W), top_y + box_h)], outline=color, width=3)
        cx, cy = r["detail_click"] if r["expanded"] else r["expand_click"]
        draw.ellipse([(cx - 16, cy - 16), (cx + 16, cy + 16)], outline="red", width=3)
        draw.line([(cx - 24, cy), (cx + 24, cy)], fill="red", width=2)
        draw.line([(cx, cy - 24), (cx, cy + 24)], fill="red", width=2)
    canvas.save(save_path)

def sanitize_filename(name):
    return re.sub(r'[\\/*?:\x22<>|@\s]', '_', name)

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
    time.sleep(0.6)

    box = get_box(hwnd)
    W, H = box["width"], box["height"]
    if W <= 100 or H <= 100:
        print("[ERROR] 游戏窗口当前处于最小化状态！请先在任务栏点击还原游戏窗口。")
        input("按回车退出...")
        return

    tmpl_path = r"C:\sanguo_report_collector\tmpl_player.png"
    if not os.path.exists(tmpl_path):
        print(f"[ERROR] 缺少行定位模板文件: {tmpl_path}")
        input("按回车退出...")
        return
    tmpl_bgr = cv2.imread(tmpl_path)

    out_dir = r"C:\sanguo_report_collector\战报导出\顶层3场实测"
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 68)
    print("  【三国志·战略版】顶层 3 场战报 像素级视觉锁定实测（蓝条验展开 + 紧绑名字）")
    print("=" * 68)
    print(f"窗口尺寸: {W} x {H}, 屏幕原点: ({box['left']}, {box['top']})")
    print(f"输出目录 (已清空旧文件): {out_dir}\n")

    # If currently in detail view, return to list first
    init_screen = capture_box(box)
    if is_in_detail(init_screen):
        print("[初始化] 当前在回放详情页，先返回战报列表...")
        return_to_list(box)
        time.sleep(0.6)

    # Scan initial screen
    print("📸 [步骤 1] 正在对当前战报列表进行像素级行定位与单行裁剪识图...")
    screen0 = capture_box(box)
    rows0 = scan_battle_rows(screen0, tmpl_bgr)

    if not rows0:
        print("\n❌ [错误] 当前画面未检测到任何属于您的战报行！")
        print("   请确认游戏已打开【战报】->【交战】列表页面，且未被其他窗口遮挡。")
        screen0.save(os.path.join(out_dir, "ERROR_当前非战报列表画面.png"))
        input("\n按回车退出...")
        return

    draw_debug_plan(screen0, rows0, os.path.join(out_dir, "00_初始画面行定位规划图.png"))

    print("\n" + "=" * 68)
    print(f"📋 【当前画面从上到下检测到的全部战报行】(共 {len(rows0)} 行)")
    print("=" * 68)
    for r in rows0:
        state_str = "🟢 已展开大卡" if r["expanded"] else "🔵 折叠细条  "
        print(f"  第 {r['index']} 行 | Y={r['y']:4d} | 状态: {state_str} (蓝红差={r['bmr']:+4.0f}) | 对手: 【{r['opponent']}】")

    # Take top 3 rows as our target list
    targets = [r["opponent"] for r in rows0[:3]]
    print("\n" + "=" * 68)
    print(f"🎯 本次锁定采集最顶层的前 {len(targets)} 场战报，严格顺序如下：")
    for idx, tname in enumerate(targets, 1):
        print(f"   计划 #{idx} -> 对手: 【{tname}】")
    print("=" * 68)

    captured_count = 0

    for step_idx, target_name in enumerate(targets, 1):
        print(f"\n▶ [执行 #{step_idx}/{len(targets)}] 目标对手: 【{target_name}】")

        # 1. Re-scan live screen to find where target_name is RIGHT NOW
        cur_screen = capture_box(box)
        cur_rows = scan_battle_rows(cur_screen, tmpl_bgr)

        # Match primarily at expected row index (step_idx - 1) so even identical opponent names in 鏖战 don't collide
        matched_row = None
        if step_idx - 1 < len(cur_rows) and same_opponent(cur_rows[step_idx - 1]["opponent"], target_name):
            matched_row = cur_rows[step_idx - 1]
        if not matched_row:
            for r in cur_rows:
                if same_opponent(r["opponent"], target_name):
                    matched_row = r
                    break
        if not matched_row and step_idx - 1 < len(cur_rows):
            matched_row = cur_rows[step_idx - 1]

        if not matched_row:
            print(f"  ❌ 未能在当前画面找到【{target_name}】，跳过！")
            continue

        print(f"  -> 实时定位到【{matched_row['opponent']}】位于 Y={matched_row['y']} | 当前展开状态: {matched_row['expanded']} (蓝红差={matched_row['bmr']:+.0f})")

        # 2. If collapsed, click row to expand it, then RE-SCAN to get its new Y after accordion shift!
        if not matched_row["expanded"]:
            ex_x, ex_y = matched_row["expand_click"]
            print(f"  -> 当前为折叠条，点击 (X={ex_x}, Y={ex_y}) 展开手风琴...")
            real_click(box["left"] + ex_x, box["top"] + ex_y, hold_sec=0.15)
            time.sleep(1.0)  # Wait for accordion animation

            # Re-scan after accordion shift!
            after_exp_screen = capture_box(box)
            after_rows = scan_battle_rows(after_exp_screen, tmpl_bgr)
            draw_debug_plan(after_exp_screen, after_rows, os.path.join(out_dir, f"0{step_idx}_展开后重定位_{sanitize_filename(target_name)}.png"))

            # Find target row in the newly shifted screen (prioritizing expected index with expanded=True)
            shifted_row = None
            if step_idx - 1 < len(after_rows) and after_rows[step_idx - 1]["expanded"] and same_opponent(after_rows[step_idx - 1]["opponent"], target_name):
                shifted_row = after_rows[step_idx - 1]
            if not shifted_row:
                for r in after_rows:
                    if r["expanded"] and same_opponent(r["opponent"], target_name):
                        shifted_row = r
                        break
            if not shifted_row and step_idx - 1 < len(after_rows):
                shifted_row = after_rows[step_idx - 1]

            if not shifted_row:
                print(f"  ❌ 展开后未能重定位到【{target_name}】！")
                continue

            matched_row = shifted_row
            print(f"  -> 手风琴展开完成！【{matched_row['opponent']}】最新位置移至 Y={matched_row['y']} | 展开状态={matched_row['expanded']} (蓝红差={matched_row['bmr']:+.0f})")

        # 3. Click (960, matched_row['y'] + 150) to enter detail screen!
        det_x, det_y = matched_row["detail_click"]
        print(f"  -> 瞄准【{matched_row['opponent']}】专属大卡片中央【胜/平/败】 (X={det_x}, Y={det_y}) 点击进入回放详情...")
        real_click(box["left"] + det_x, box["top"] + det_y, hold_sec=0.15)

        # 4. Wait & verify detail screen
        entered = False
        detail_img = None
        for _ in range(10):
            time.sleep(0.25)
            chk = capture_box(box)
            if is_in_detail(chk):
                entered = True
                time.sleep(0.3)  # Let detail animation settle
                detail_img = capture_box(box)
                break

        if entered and detail_img:
            captured_count += 1
            safe_name = sanitize_filename(matched_row["opponent"])
            fname = f"第{step_idx}场_{safe_name}.png"
            fpath = os.path.join(out_dir, fname)
            detail_img.save(fpath)
            print(f"  -> ✅ 成功截取并保存全屏详情图: {fname}")

            # 5. Return to list
            return_to_list(box)
            time.sleep(0.6)
        else:
            print(f"  -> ❌ 点击后未能进入详情页！")
            return_to_list(box)

    print("\n" + "=" * 68)
    print(f"🎉 实测完成！成功按顺序采集 {captured_count}/{len(targets)} 场顶层战报！")
    print(f"📂 请查看结果目录: {out_dir}")
    print("=" * 68)
    input("\n按【回车键】关闭窗口...")

if __name__ == "__main__":
    main()
