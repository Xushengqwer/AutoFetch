import os
import sys
import time
import re
import json
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

pyautogui.FAILSAFE = True
sys.stdout.reconfigure(encoding='utf-8')

def attach_desktop():
    try:
        desk = win32service.OpenDesktop('default', 0, False, win32con.GENERIC_ALL)
        desk.SetThreadDesktop()
    except Exception:
        pass

attach_desktop()

BASE_DIR = r"C:\sanguo_report_collector"
TARGET_DATE = "2026-10-24"  # 本次目标：只采 2026-10-24 当天全部战报（跳过 25 号，遇到 23 号停止）

def load_bgr_template(filename):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return None
    pil_im = Image.open(path).convert("RGB")
    return cv2.cvtColor(np.array(pil_im), cv2.COLOR_RGB2BGR)

# Universal UI Templates (100% Account-Agnostic)
TMPL_MENG = load_bgr_template("tmpl_meng.png")                 # 左侧通用【盟】金标
TMPL_BTN_REPORT = load_bgr_template("tmpl_btn_report.png")     # 大地图左下角【战报】按钮
TMPL_BTN_BACK = load_bgr_template("tmpl_btn_back.png")         # 右上角通用金色【返回】按钮
TMPL_TITLE_LIST = load_bgr_template("tmpl_title_report.png")   # 左上角【个人战报】标题
TMPL_BTN_REPLAY = load_bgr_template("tmpl_btn_replay.png")     # 详情页左上角【战斗回放】按钮
TMPL_POPUP_CLOSE = load_bgr_template("tmpl_popup_close.png")   # 战法预览等弹窗右上角【×】关闭按钮

SESSION_PLAYER_TMPL = None

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

def zero_inertia_scroll_up(box, drag_distance_ratio=0.20):
    W, H = box["width"], box["height"]
    drag_x = box["left"] + int(W * 0.48)
    start_y = box["top"] + int(H * 0.74)
    end_y = box["top"] + int(H * (0.74 - drag_distance_ratio))

    pyautogui.moveTo(drag_x, start_y, duration=0.12)
    time.sleep(0.05)
    pyautogui.mouseDown(drag_x, start_y, button='left')
    time.sleep(0.05)

    steps = 12
    for s in range(1, steps + 1):
        cur_y = int(start_y + (end_y - start_y) * (s / steps))
        pyautogui.moveTo(drag_x, cur_y, duration=0.03)

    time.sleep(0.12)
    pyautogui.moveTo(drag_x, end_y - 1, duration=0.08)
    pyautogui.moveTo(drag_x, end_y, duration=0.08)
    time.sleep(0.08)
    pyautogui.mouseUp(drag_x, end_y, button='left')
    time.sleep(0.55)

def measure_scroll_shift(screen_before_pil, screen_after_pil):
    arr1 = cv2.cvtColor(np.array(screen_before_pil), cv2.COLOR_RGB2GRAY)
    arr2 = cv2.cvtColor(np.array(screen_after_pil), cv2.COLOR_RGB2GRAY)
    H, W = arr1.shape

    y1_after, y2_after = int(0.26 * H), int(0.52 * H)
    x1, x2 = int(0.08 * W), int(0.90 * W)
    tmpl = arr2[y1_after:y2_after, x1:x2]

    search_y1, search_y2 = int(0.18 * H), int(0.92 * H)
    roi = arr1[search_y1:search_y2, x1:x2]

    res = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    matched_y1_before = search_y1 + max_loc[1]
    delta_y = matched_y1_before - y1_after
    return delta_y, float(max_val)

def ocr_pil(pil_img):
    async def _ocr():
        return await winocr.recognize_pil(pil_img, lang="zh-Hans")
    return asyncio.run(_ocr()).lines

def match_roi(img_bgr, tmpl, x1r, x2r, y1r, y2r):
    if tmpl is None:
        return 0.0, (0, 0)
    H, W = img_bgr.shape[:2]
    sx, sy = W / 1920.0, H / 1080.0
    tw, th = max(8, int(tmpl.shape[1] * sx)), max(8, int(tmpl.shape[0] * sy))
    tm = cv2.resize(tmpl, (tw, th))
    roi = img_bgr[int(y1r * H):int(y2r * H), int(x1r * W):int(x2r * W)]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return 0.0, (0, 0)
    res = cv2.matchTemplate(roi, tm, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    cx = int(x1r * W) + max_loc[0] + tw // 2
    cy = int(y1r * H) + max_loc[1] + th // 2
    return float(max_val), (cx, cy)

def find_text_in_roi(screen_pil, x1r, y1r, x2r, y2r, keywords, pad=25):
    """
    Takes a live screenshot crop of (x1r..x2r, y1r..y2r), runs OCR, and returns
    the EXACT pixel center (cx, cy) of the matched keyword on the user's current screen!
    100% resolution-independent — never relies on hardcoded coordinates.
    """
    W, H = screen_pil.size
    x1, y1, x2, y2 = int(x1r * W), int(y1r * H), int(x2r * W), int(y2r * H)
    arr = np.array(screen_pil.crop((x1, y1, x2, y2)))
    if arr.size == 0:
        return False, (0, 0), ""
    rep = cv2.copyMakeBorder(arr, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    big = Image.fromarray(rep).resize((rep.shape[1] * 2, rep.shape[0] * 2), Image.Resampling.BICUBIC)
    for l in ocr_pil(big):
        t = l.text.replace(' ', '')
        for kw in keywords:
            if kw in t and l.words:
                kw_words = [w for w in l.words if any(c in w.text for c in kw)]
                use_words = kw_words if kw_words else l.words
                xmin = min(w.bounding_rect.x for w in use_words) / 2.0 - pad
                ymin = min(w.bounding_rect.y for w in use_words) / 2.0 - pad
                xmax = max(w.bounding_rect.x + w.bounding_rect.width for w in use_words) / 2.0 - pad
                ymax = max(w.bounding_rect.y + w.bounding_rect.height for w in use_words) / 2.0 - pad
                cx = int(x1 + (xmin + xmax) / 2.0)
                cy = int(y1 + (ymin + ymax) / 2.0)
                return True, (cx, cy), t
    return False, (0, 0), ""

def locate_popup_close_btn(screen_pil, img_bgr):
    """
    Dynamically locates the [X] close button on a modal popup (such as '战法预览')
    from the live screenshot using template matching + red-brown square color contour fallback.
    """
    W, H = screen_pil.size
    s_close, pt_close = match_roi(img_bgr, TMPL_POPUP_CLOSE, 0.65, 0.98, 0.05, 0.35)
    if s_close > 0.68:
        return True, pt_close, s_close

    # Fallback: scan top-right popup region (X=0.72..0.95, Y=0.08..0.28) for the red-brown [X] square
    arr = np.array(screen_pil)
    x1, x2 = int(0.72 * W), int(0.95 * W)
    y1, y2 = int(0.08 * H), int(0.28 * H)
    roi = arr[y1:y2, x1:x2]
    if roi.size > 0:
        r = roi[:, :, 0].astype(np.int32)
        g = roi[:, :, 1].astype(np.int32)
        b = roi[:, :, 2].astype(np.int32)
        mask = (r - b > 35) & (r > 85) & (g < 90)
        ys, xs = np.where(mask)
        if len(xs) > 40:
            cx = x1 + int(np.median(xs))
            cy = y1 + int(np.median(ys))
            return True, (cx, cy), 0.75

    return False, (0, 0), float(s_close)

def detect_screen_state(screen_pil):
    """
    Pure Live-Screenshot + OCR + Visual State Machine:
    1. Checks if a modal popup ('战法预览', '战法品质', '战法说明' or [X] close button) is covering the screen.
    2. Checks if on '战斗回放' detail page.
    3. Checks if on '个人战报' list page AND verifies both '交战' active tab AND '只看个人' filter via OCR!
    4. Checks if on '大地图' World Map via live OCR of '战报'.
    """
    arr_rgb = np.array(screen_pil)
    img_bgr = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2BGR)
    H, W = arr_rgb.shape[:2]

    # 0. Check for modal popup ('战法预览' etc.) FIRST because it overlays on top of '战斗回放'!
    f_pop_ocr, _, pop_txt = find_text_in_roi(
        screen_pil, 0.25, 0.08, 0.75, 0.38, ["战法预览", "战法品质", "战法说明", "发动概率"]
    )
    has_close, close_pt, s_close = locate_popup_close_btn(screen_pil, img_bgr)
    if f_pop_ocr or s_close > 0.76:
        if not has_close:
            # If OCR found '战法预览' but color box didn't trigger, use relative popup top-right
            close_pt = (int(0.880 * W), int(0.138 * H))
        return {
            "state": "POPUP_MODAL",
            "close_pt": close_pt,
            "popup_text": pop_txt or "弹窗预览",
            "scores": ("POPUP_MODAL", pop_txt, round(s_close, 2)),
        }

    # 1. Check top-right '返回' button via live OCR + template fallback
    f_back, pt_back_ocr, _ = find_text_in_roi(screen_pil, 0.84, 0.0, 1.0, 0.11, ["返回"])
    s_back, pt_back_tmpl = match_roi(img_bgr, TMPL_BTN_BACK, 0.85, 0.99, 0.0, 0.11)
    has_back = f_back or (s_back > 0.68)
    back_pt = pt_back_ocr if f_back else (pt_back_tmpl if s_back > 0.60 else (int(0.945 * W), int(0.038 * H)))

    # 2. Check top-left for '战斗回放' (Detail screen)
    f_replay, _, _ = find_text_in_roi(screen_pil, 0.01, 0.0, 0.25, 0.11, ["战斗回放", "回放"])
    s_replay, _ = match_roi(img_bgr, TMPL_BTN_REPLAY, 0.01, 0.22, 0.0, 0.11)
    if f_replay or s_replay > 0.68:
        return {
            "state": "REPORT_DETAIL",
            "back_pt": back_pt,
            "scores": ("DETAIL", round(s_replay, 2)),
        }

    # 3. Check top-left for '个人战报' vs '同盟战报'
    f_title, _, title_txt = find_text_in_roi(screen_pil, 0.01, 0.0, 0.25, 0.11, ["个人战报", "同盟战报"])
    s_title, _ = match_roi(img_bgr, TMPL_TITLE_LIST, 0.01, 0.22, 0.0, 0.11)
    if f_title or s_title > 0.68:
        # If accidentally in '同盟战报' instead of '个人战报', treat as subpage to exit!
        if "同盟" in title_txt and "个人" not in title_txt and s_title < 0.75:
            return {
                "state": "OTHER_SUBPAGE",
                "back_pt": back_pt,
                "scores": ("ALLIANCE_REPORT", title_txt),
            }

        # Dynamically locate the '交战' tab via live OCR on the tab bar!
        f_jz, pt_jz, _ = find_text_in_roi(screen_pil, 0.03, 0.11, 0.60, 0.19, ["交战"])
        jiaozhan_pt = pt_jz if f_jz else (int(0.267 * W), int(0.148 * H))

        # Check gold highlight underline right below the located '交战' tab!
        jx = jiaozhan_pt[0]
        tab_patch = arr_rgb[int(0.166 * H):int(0.177 * H), max(0, jx - int(0.035 * W)):min(W, jx + int(0.035 * W))]
        mean_rgb = tab_patch.mean(axis=(0, 1)) if tab_patch.size > 0 else [0, 0, 0]
        rmb = float(mean_rgb[0]) - float(mean_rgb[2])
        if rmb <= 22.0:
            return {
                "state": "REPORT_LIST_OTHER_TAB",
                "jiaozhan_pt": jiaozhan_pt,
                "back_pt": back_pt,
                "scores": ("LIST_OTHER_TAB", round(rmb, 1)),
            }

        # Verify '只看个人' filter on the right side of the navigation bar via live OCR!
        f_only, pt_only, only_txt = find_text_in_roi(
            screen_pil, 0.72, 0.11, 0.89, 0.19, ["只看个人", "个人"]
        )
        if not f_only:
            # Check if another filter text (e.g. '全部' or '同盟') is shown in the dropdown box
            _, pt_drop, drop_txt = find_text_in_roi(
                screen_pil, 0.72, 0.11, 0.89, 0.19, ["全部", "同盟", "只看"]
            )
            if drop_txt and "个人" not in drop_txt:
                return {
                    "state": "REPORT_LIST_WRONG_FILTER",
                    "filter_pt": pt_drop if pt_drop != (0, 0) else (int(0.81 * W), int(0.148 * H)),
                    "back_pt": back_pt,
                    "scores": ("WRONG_FILTER", drop_txt),
                }

        return {
            "state": "REPORT_LIST_PVP",
            "jiaozhan_pt": jiaozhan_pt,
            "only_personal_pt": pt_only,
            "back_pt": back_pt,
            "scores": ("LIST_PVP", round(rmb, 1), only_txt or "只看个人"),
        }

    # 4. Check bottom-left menu on World Map for '战报' via live OCR!
    f_world_ocr, pt_world_ocr, _ = find_text_in_roi(screen_pil, 0.0, 0.65, 0.16, 0.98, ["战报"])
    s_world_tmpl, pt_world_tmpl = match_roi(img_bgr, TMPL_BTN_REPORT, 0.0, 0.15, 0.72, 0.90)
    if f_world_ocr or s_world_tmpl > 0.70:
        report_btn_pt = pt_world_ocr if f_world_ocr else pt_world_tmpl
        return {
            "state": "WORLD_MAP",
            "report_btn_pt": report_btn_pt,
            "scores": ("WORLD_MAP", f_world_ocr, round(s_world_tmpl, 2)),
        }

    if has_back:
        return {
            "state": "OTHER_SUBPAGE",
            "back_pt": back_pt,
            "scores": ("OTHER_SUBPAGE", f_back, round(s_back, 2)),
        }

    return {
        "state": "UNKNOWN_OR_POPUP",
        "back_pt": back_pt,
        "scores": ("UNKNOWN",),
    }

def save_nav_debug(screen_pil, pt, save_path):
    if not save_path:
        return
    try:
        canvas = screen_pil.copy()
        draw = ImageDraw.Draw(canvas)
        cx, cy = pt
        draw.ellipse([(cx - 18, cy - 18), (cx + 18, cy + 18)], outline="red", width=3)
        draw.line([(cx - 26, cy), (cx + 26, cy)], fill="red", width=2)
        draw.line([(cx, cy - 26), (cx, cy + 26)], fill="red", width=2)
        canvas.save(save_path)
    except Exception:
        pass

def ensure_on_pvp_list(box, debug_dir=None, max_recover_steps=10):
    for step in range(1, max_recover_steps + 1):
        screen = capture_box(box)
        info = detect_screen_state(screen)
        st = info["state"]

        if st == "REPORT_LIST_PVP":
            return True, screen

        if st == "POPUP_MODAL":
            cx, cy = info["close_pt"]
            if debug_dir:
                save_nav_debug(screen, (cx, cy), os.path.join(debug_dir, f"nav_{step:02d}_close_popup.png"))
            print(f"  🛡️ [弹窗自愈 #{step}] 实时截屏识别到【{info.get('popup_text', '战法预览')}】弹窗，锁定右上角【×】按钮 (X={cx}, Y={cy}) 点击关闭...")
            real_click(box["left"] + cx, box["top"] + cy, hold_sec=0.15)
            time.sleep(0.65)
            continue

        if st == "WORLD_MAP":
            rx, ry = info["report_btn_pt"]
            if debug_dir:
                save_nav_debug(screen, (rx, ry), os.path.join(debug_dir, f"nav_{step:02d}_worldmap_click_report.png"))
            print(f"  🧭 [视觉导航 #{step}] 实时截屏识图锁定【战报】按钮位于 (X={rx}, Y={ry})，正在点击进入...")
            real_click(box["left"] + rx, box["top"] + ry, hold_sec=0.15)
            time.sleep(1.0)
            continue

        if st == "REPORT_LIST_OTHER_TAB":
            jx, jy = info["jiaozhan_pt"]
            if debug_dir:
                save_nav_debug(screen, (jx, jy), os.path.join(debug_dir, f"nav_{step:02d}_switch_jiaozhan_tab.png"))
            print(f"  🧭 [视觉导航 #{step}] 当前在【个人战报】但未选中【交战】，实时识图锁定【交战】标签 (X={jx}, Y={jy}) 点击切换...")
            real_click(box["left"] + jx, box["top"] + jy, hold_sec=0.15)
            time.sleep(0.8)
            continue

        if st == "REPORT_LIST_WRONG_FILTER":
            fx, fy = info["filter_pt"]
            print(f"  🧭 [视觉导航 #{step}] 当前未处于【只看个人】过滤状态，点击右上角筛选框 (X={fx}, Y={fy}) 切换为【只看个人】...")
            real_click(box["left"] + fx, box["top"] + fy, hold_sec=0.15)
            time.sleep(0.5)
            s_drop = capture_box(box)
            f_p, pt_p, _ = find_text_in_roi(s_drop, 0.70, 0.15, 0.90, 0.35, ["只看个人", "个人"])
            if f_p:
                real_click(box["left"] + pt_p[0], box["top"] + pt_p[1], hold_sec=0.15)
                time.sleep(0.6)
            continue

        if st == "REPORT_DETAIL":
            bx, by = info["back_pt"]
            print(f"  🧭 [视觉导航 #{step}] 当前在【战斗回放详情页】，点击右上角【返回】退回列表 (X={bx}, Y={by})...")
            real_click(box["left"] + bx, box["top"] + by, hold_sec=0.15)
            time.sleep(0.75)
            continue

        if st == "OTHER_SUBPAGE":
            bx, by = info["back_pt"]
            print(f"  🛡️ [异常自救 #{step}] 检测到误入其他子页面 ({info['scores']})，点击右上角【返回】归位 (X={bx}, Y={by})...")
            real_click(box["left"] + bx, box["top"] + by, hold_sec=0.15)
            time.sleep(0.8)
            continue

        print(f"  🛡️ [异常自救 #{step}] 当前画面状态未知或存在遮罩 (scores={info['scores']})，尝试点击右上角【返回】...")
        bx, by = info["back_pt"]
        real_click(box["left"] + bx, box["top"] + by, hold_sec=0.15)
        time.sleep(0.8)

    return False, capture_box(box)

def diagnose_and_recover_via_screenshot(box, debug_dir, reason="", target_name=""):
    """
    截图驱动的全局错误诊断与自愈机制：
    无论何时遇到点击未响应、坐标漂移、未能展开、或未能进入详情页等异常，
    第一时间【重新截图】，通过视觉+OCR看清当前画面到底处于什么状态：
      1. 看有没有弹窗【×】号（如战法预览、系统弹窗） -> 有则精准点【×】关闭；
      2. 看右上角有没有【返回】（如已进详情页或误入子页） -> 有则精准点【返回】退回列表；
      3. 看标签栏是否偏离【交战】或【只看个人】 -> 偏离则精准点回；
      4. 回到列表后再次截图扫描全屏战报行，打印目标人名漂移后的最新实时坐标 (X, Y)！
    """
    print(f"\n  📸 [截图错误自检] 触发原因: {reason or '操作异常'} — 正在立即重新截图诊断画面...")
    diag_screen = capture_box(box)
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        ts_str = datetime.now().strftime("%H%M%S_%f")[:10]
        diag_path = os.path.join(debug_dir, f"error_diag_{ts_str}.png")
        try:
            diag_screen.save(diag_path)
        except Exception:
            pass

    st_info = detect_screen_state(diag_screen)
    st = st_info["state"]
    print(f"  🔍 [截图诊断结果] 当前画面状态={st} | 视觉匹配度={st_info['scores']}")

    if st == "POPUP_MODAL":
        cx, cy = st_info["close_pt"]
        print(f"  🛠️ [看图纠错] 截图发现【{st_info.get('popup_text', '模态弹窗')}】遮挡，找到右上角【×】位于 (X={cx}, Y={cy})，立即点击关闭！")
        real_click(box["left"] + cx, box["top"] + cy, hold_sec=0.15)
        time.sleep(0.55)

    ok, clean_list_screen = ensure_on_pvp_list(box, debug_dir)
    if ok and target_name:
        fresh_rows = scan_battle_rows(clean_list_screen)
        matched = [r for r in fresh_rows if same_opponent(r["opponent"], target_name)]
        if matched:
            mr = matched[0]
            print(
                f"  📍 [看图重定位] 已在最新截图中重新锁定【{target_name}】: "
                f"最新 Y={mr['y']}, 展开状态={mr['expanded']}, 人名点击坐标={mr['expand_click']}"
            )
        else:
            print(f"  📍 [看图重定位] 当前视野共 {len(fresh_rows)} 行，将在主循环通过内存队列重新对齐【{target_name}】。")
    return ok, clean_list_screen

def read_card_datetime(screen_pil, ry):
    """
    Reads the exact battle timestamp (e.g. '2026年10月24日 18:23:04') from the bottom
    center of an EXPANDED battle card whose header is at Y = ry.
    Always called on the CLEAN un-annotated screenshot!
    Returns (date_str 'YYYY-MM-DD', time_str 'HH:MM:SS', raw_text).
    """
    W, H = screen_pil.size
    y1 = max(0, min(H - 10, ry + int(0.240 * H)))
    y2 = max(y1 + 10, min(H, ry + int(0.308 * H)))
    x1, x2 = int(0.36 * W), int(0.64 * W)
    crop = screen_pil.crop((x1, y1, x2, y2))

    big1 = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.BICUBIC)
    arr = np.array(crop)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    boost = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    big2 = Image.fromarray(boost).resize((crop.width * 3, crop.height * 3), Image.Resampling.BICUBIC)

    raw_texts = []
    for img_pass in (big1, big2):
        lines = ocr_pil(img_pass)
        for l in lines:
            t = l.text.replace(' ', '')
            raw_texts.append(t)
            t_norm = t.replace('目', '月').replace('四', '24')
            m = re.search(
                r'(202\d|\d{2,4})[年\.\-/](\d{1,2})[月\.\-/](\d{1,2})[日号]?(?:.*?(\d{1,2})[:：\.]+(\d{2})[:：\.]+(\d{2}))?',
                t_norm
            )
            if m:
                yyyy, mm, dd, hh, mi, ss = m.groups()
                mm_i, dd_i = int(mm), int(dd)
                if 1 <= mm_i <= 12 and 1 <= dd_i <= 31:
                    date_str = f"2026-{mm_i:02d}-{dd_i:02d}"
                    time_str = f"{int(hh):02d}:{int(mi):02d}:{int(ss):02d}" if (hh and mi and ss) else ""
                    return date_str, time_str, " / ".join(raw_texts)
    return None, None, " / ".join(raw_texts)

def clean_cjk(text):
    t = re.sub(r'^[0O@演`廊一一\]\|丨，,。\.\s]+', '', text).strip()
    t = re.sub(r'[\|丨I_\[\]]+', '', t)
    return "".join(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]', t))

def edit_distance(s1, s2):
    if len(s1) < len(s2):
        return edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def same_opponent(name_a, name_b):
    ca = clean_cjk(name_a)
    cb = clean_cjk(name_b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    dist = edit_distance(ca, cb)
    if min(len(ca), len(cb)) >= 3 and dist <= 1:
        if max(len(ca), len(cb)) <= 4:
            return ca[-1] == cb[-1]
        return True
    if min(len(ca), len(cb)) >= 5 and dist <= 2 and ca[-2:] == cb[-2:]:
        return True
    return False

def find_candidate_ys(img_bgr, tmpl, x1r, x2r, y1r, y2r, thresh=0.72, scale_to_1080=True):
    if tmpl is None:
        return []
    H, W = img_bgr.shape[:2]
    if scale_to_1080:
        sx, sy = W / 1920.0, H / 1080.0
        tw, th = max(8, int(tmpl.shape[1] * sx)), max(8, int(tmpl.shape[0] * sy))
        tm = cv2.resize(tmpl, (tw, th))
    else:
        tm = tmpl
        th, tw = tm.shape[:2]

    y_start, y_end = int(y1r * H), int(y2r * H)
    x_start, x_end = int(x1r * W), int(x2r * W)
    roi = img_bgr[y_start:y_end, x_start:x_end]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return []

    res = cv2.matchTemplate(roi, tm, cv2.TM_CCOEFF_NORMED)
    min_gap = int(0.038 * H)
    ys = []
    for y in range(res.shape[0]):
        score = float(res[y, :].max())
        if score > thresh:
            real_y = y_start + y + th // 2
            if not ys or (real_y - ys[-1][0]) > min_gap:
                ys.append([real_y, score])
            elif score > ys[-1][1]:
                ys[-1] = [real_y, score]
    return ys

def scan_battle_rows(screen_pil):
    """
    Scans the current battle list screenshot for all visible rows (Y in 0.19*H .. 0.895*H).
    - Automatically learns the logged-in player's name template on first run (100% account-agnostic).
    - Reads each opponent's name via OCR and computes the exact live coordinates of the opponent's name
      for clicking (`expand_click`).
    - Also detects top-clipped expanded cards whose header bar shifted slightly under the top tab bar
      via the left blue '我方' vertical banner (`X = 0.043*W..0.053*W`), so no expanded card is ever missed!
    """
    global SESSION_PLAYER_TMPL
    arr = np.array(screen_pil)
    img_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    W, H = screen_pil.size

    # Scan headers up to 0.925*H (999px in 1080p) so all visible rows in '个人战报 -> 交战' are detected.
    ys_meng = find_candidate_ys(img_bgr, TMPL_MENG, 0.06, 0.10, 0.19, 0.925, thresh=0.70, scale_to_1080=True)

    if not ys_meng and SESSION_PLAYER_TMPL is None:
        bx1, bx2 = int(0.038 * W), int(0.055 * W)
        strip = arr[int(0.18 * H):int(0.89 * H), bx1:bx2]
        bmr_col = strip[:, :, 2].astype(np.float32).mean(axis=1) - strip[:, :, 0].astype(np.float32).mean(axis=1)
        blue_idx = np.where(bmr_col > 30.0)[0]
        if len(blue_idx) > 20:
            seed_ry = int(0.18 * H) + int(blue_idx[0]) + int(0.018 * H)
            ys_meng.append([seed_ry, 0.85])

    if SESSION_PLAYER_TMPL is None and ys_meng:
        seed_ry = ys_meng[0][0]
        py1 = max(0, seed_ry - int(0.018 * H))
        py2 = min(H, seed_ry + int(0.018 * H))
        px1, px2 = int(0.275 * W), int(0.375 * W)
        SESSION_PLAYER_TMPL = img_bgr[py1:py2, px1:px2].copy()
        p_crop = arr[py1:py2, px1:px2]
        p_rep = cv2.copyMakeBorder(p_crop, 35, 35, 25, 25, cv2.BORDER_REPLICATE)
        p_big = Image.fromarray(p_rep).resize((p_rep.shape[1] * 2, p_rep.shape[0] * 2), Image.Resampling.BICUBIC)
        p_name = "".join([l.text.replace(' ', '') for l in ocr_pil(p_big)])
        p_name_clean = re.sub(r'^[0O@演`廊一一\]\|丨]+|[0O@演`廊一一\]\|丨]+$', '', p_name).strip()
        print(f"  🤖 [账号自适应] 已从首屏战报自动学习当前主公特征模板 (识别主公: 【{p_name_clean or '当前登录主公'}】)")

    ys_player = []
    if SESSION_PLAYER_TMPL is not None:
        ys_player = find_candidate_ys(img_bgr, SESSION_PLAYER_TMPL, 0.26, 0.38, 0.19, 0.925, thresh=0.72, scale_to_1080=False)

    min_gap = int(0.038 * H)
    all_candidates = sorted(ys_meng + ys_player, key=lambda x: x[0])
    ys = []
    for ry, sc in all_candidates:
        if not ys or (ry - ys[-1][0]) > min_gap:
            ys.append([ry, sc])
        elif sc > ys[-1][1]:
            ys[-1] = [ry, sc]

    rows = []
    half_h = max(16, int(0.021 * H))
    pad = 20
    for idx, (ry, sc) in enumerate(ys):
        if idx + 1 < len(ys) and (ys[idx + 1][0] - ry) < int(0.18 * H):
            is_expanded = False
            bmr = -50.0
        else:
            by1 = min(H - 1, ry + int(0.018 * H))
            by2 = min(H - 1, ry + int(0.075 * H))
            bx1, bx2 = int(0.038 * W), int(0.055 * W)
            patch = arr[by1:by2, bx1:bx2]
            mean_rgb = patch.mean(axis=(0, 1)) if patch.size > 0 else [0, 0, 0]
            bmr = float(mean_rgb[2]) - float(mean_rgb[0])
            is_expanded = bmr > 30.0

        enemy_meng_sc, _ = match_roi(
            img_bgr,
            TMPL_MENG,
            0.89,
            0.94,
            max(0.0, (ry - int(0.025 * H)) / H),
            min(1.0, (ry + int(0.025 * H)) / H),
        )
        has_enemy_alliance = enemy_meng_sc > 0.65

        ox1, ox2 = int(0.59 * W), int(0.78 * W)
        oy1, oy2 = max(0, ry - half_h), min(H, ry + half_h)
        crop = arr[oy1:oy2, ox1:ox2]
        rep = cv2.copyMakeBorder(crop, 35, 35, pad, pad, cv2.BORDER_REPLICATE)
        pil_rep = Image.fromarray(rep)
        big = pil_rep.resize((pil_rep.width * 2, pil_rep.height * 2), Image.Resampling.BICUBIC)
        ores = ocr_pil(big)
        opp_raw = "".join([l.text.replace(' ', '') for l in ores])
        opp_clean = re.sub(r'^[0O@演`廊一一，,。\.]+', '', opp_raw).strip()
        if not opp_clean and crop.size > 0:
            # Fallback contrast-boosted pass for short 2-char names like '劍丨酒'
            g_crop = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
            g_boost = cv2.normalize(g_crop, None, 0, 255, cv2.NORM_MINMAX)
            g_rep = cv2.copyMakeBorder(g_boost, 35, 35, pad, pad, cv2.BORDER_REPLICATE)
            g_big = Image.fromarray(g_rep).resize((g_rep.shape[1] * 2, g_rep.shape[0] * 2), Image.Resampling.BICUBIC)
            ores2 = ocr_pil(g_big)
            opp_raw2 = "".join([l.text.replace(' ', '') for l in ores2])
            opp_clean = re.sub(r'^[0O@演`廊一一，,。\.]+', '', opp_raw2).strip()
            if ores2 and not ores:
                ores = ores2
        if not opp_clean:
            opp_clean = f"未知对手_{idx+1}"

        # Calculate the live pixel center of the opponent's name so we click directly on the opponent's name!
        opp_cx = int(0.67 * W)
        for l in ores:
            if l.words:
                xmin = min(w.bounding_rect.x for w in l.words) / 2.0 - pad
                xmax = max(w.bounding_rect.x + w.bounding_rect.width for w in l.words) / 2.0 - pad
                cand_cx = int(ox1 + (xmin + xmax) / 2.0)
                if int(0.60 * W) <= cand_cx <= int(0.77 * W):
                    opp_cx = cand_cx
                    break

        rows.append({
            "index": idx + 1,
            "y": ry,
            "score": sc,
            "expanded": is_expanded,
            "top_clipped": False,
            "bmr": bmr,
            "opponent": opp_clean,
            "has_enemy_alliance": has_enemy_alliance,
            "expand_click": (opp_cx, ry),
            "detail_click": (int(0.50 * W), ry + int(0.14 * H)),
        })

    # Also check if there is a top-clipped expanded card whose header bar shifted above Y = 0.19*H
    strip = arr[int(0.18 * H):int(0.55 * H), int(0.043 * W):int(0.053 * W)]
    if strip.size > 0:
        r_m = strip[:, :, 0].astype(np.float32).mean(axis=1)
        g_m = strip[:, :, 1].astype(np.float32).mean(axis=1)
        b_m = strip[:, :, 2].astype(np.float32).mean(axis=1)
        idx_blue = np.where((b_m - r_m > 30.0) & (g_m < 170.0))[0]
        if len(idx_blue) > 0:
            gaps = np.where(np.diff(idx_blue) > 12)[0]
            segments = np.split(idx_blue, gaps + 1)
            for seg in segments:
                if len(seg) > int(0.12 * H) and seg[0] < int(0.10 * H):
                    b_top = int(0.18 * H) + int(seg[0])
                    b_bot = int(0.18 * H) + int(seg[-1])
                    v_ry = b_bot - int(0.285 * H)
                    already = any(abs(r["y"] - v_ry) < int(0.08 * H) for r in rows)
                    if not already and (not rows or v_ry < rows[0]["y"] - int(0.12 * H)):
                        top_row = {
                            "index": 0,
                            "y": v_ry,
                            "score": 0.95,
                            "expanded": True,
                            "top_clipped": True,
                            "bmr": 60.0,
                            "opponent": "顶部展开卡",
                            "has_enemy_alliance": True,
                            "expand_click": (int(0.67 * W), max(int(0.20 * H), v_ry)),
                            "detail_click": (int(0.50 * W), (b_top + b_bot) // 2),
                        }
                        rows.insert(0, top_row)
                        for i, r in enumerate(rows):
                            r["index"] = i + 1
                    break

    # Assign `group_id` to distinguish rows within the SAME '鏖战' streak (single-open accordion)
    # from rows separated by a '☆ 收藏' footer bar (~0.124*H gap) or '鏖战 X 场' banner (~0.224*H gap).
    cur_group = 1
    for i, r in enumerate(rows):
        if i > 0:
            prev = rows[i - 1]
            same_grp_max_gap = int(0.35 * H) if prev["expanded"] else int(0.095 * H)
            if (r["y"] - prev["y"]) > same_grp_max_gap:
                cur_group += 1
        r["group_id"] = cur_group

    return rows

def sync_memory_queue(memory_queue, cur_rows, last_start_s=0):
    """
    Maintains the ordered in-memory queue of opponent names (`memory_queue`) with zero drift across a full day:
    - Every valid detected battle row receives an integer `qid` (never skipped or left None).
    - Restricts alignment window around `last_start_s` and requires multi-row sequence agreement.
    - Dynamically trims untouched `PENDING` rows at the tail that were temporarily pushed below the bottom
      of the screen when an upper card expanded (+275px), re-queuing them cleanly when scrolled back into view.
    """
    if not memory_queue:
        for r in cur_rows:
            if r.get("top_clipped"):
                continue
            q = {
                "qid": len(memory_queue) + 1,
                "opponent": r["opponent"],
                "status": "PENDING",
                "tries": 0,
            }
            memory_queue.append(q)
            r["qid"] = q["qid"]
        return 0, len(memory_queue)

    M = len(memory_queue)
    K = len(cur_rows)
    best_s = None
    best_score = -9999

    s_min = max(0, last_start_s - 2)
    for s in range(s_min, M):
        overlap = min(K, M - s)
        matches = 0
        mismatches = 0
        for j in range(overlap):
            if cur_rows[j].get("top_clipped"):
                continue
            if same_opponent(memory_queue[s + j]["opponent"], cur_rows[j]["opponent"]):
                matches += 1
            else:
                mismatches += 1
        if matches >= 1 and mismatches <= max(1, (matches + mismatches) // 3):
            single_overlap_penalty = 18 if (overlap == 1 and K >= 2 and M >= 2) else 0
            score = matches * 20 - mismatches * 25 - abs(s - last_start_s) * 5 - single_overlap_penalty
            if score > best_score:
                best_score = score
                best_s = s

    # Fallback if only 1 row overlapped or OCR had heavy noise: anchor by the first PENDING item
    if best_s is None:
        first_pending_idx = next((i for i, q in enumerate(memory_queue) if q["status"] == "PENDING"), M - 1)
        for j, r in enumerate(cur_rows):
            if not r.get("top_clipped") and same_opponent(r["opponent"], memory_queue[first_pending_idx]["opponent"]):
                best_s = max(0, first_pending_idx - j)
                break
    if best_s is None:
        best_s = min(max(0, last_start_s), max(0, M - 1))

    # If an upper card expanded (+275px) and pushed untouched PENDING rows at the tail off the bottom
    # of the screen, trim those off-screen untouched tail items so `memory_queue` always ends at the
    # current visible bottom of the screen (they will be cleanly re-queued when we scroll up!).
    end_idx = best_s + K
    if end_idx < len(memory_queue):
        if all(q["status"] == "PENDING" and q.get("tries", 0) == 0 for q in memory_queue[end_idx:]):
            del memory_queue[end_idx:]

    added_count = 0
    for j in range(K):
        q_idx = best_s + j
        if q_idx < len(memory_queue):
            cur_rows[j]["qid"] = memory_queue[q_idx]["qid"]
            if cur_rows[j].get("top_clipped"):
                cur_rows[j]["opponent"] = memory_queue[q_idx]["opponent"]
            elif memory_queue[q_idx]["opponent"].startswith("未知对手_") and not cur_rows[j]["opponent"].startswith("未知对手_"):
                memory_queue[q_idx]["opponent"] = cur_rows[j]["opponent"]
        else:
            if not cur_rows[j].get("top_clipped"):
                q = {
                    "qid": len(memory_queue) + 1,
                    "opponent": cur_rows[j]["opponent"],
                    "status": "PENDING",
                    "tries": 0,
                }
                memory_queue.append(q)
                cur_rows[j]["qid"] = q["qid"]
                added_count += 1

    return best_s, added_count

def draw_debug_plan(screen_pil, rows, target_qid, save_path):
    canvas = screen_pil.copy()
    draw = ImageDraw.Draw(canvas)
    W, H = canvas.size
    for r in rows:
        ry = r["y"]
        is_target = (r.get("qid") == target_qid)
        color = "lime" if r["expanded"] else ("orange" if is_target else "cyan")
        box_h = int(0.318 * H) if r["expanded"] else int(0.045 * H)
        top_y = max(int(0.18 * H), ry - int(0.022 * H))
        bot_y = min(int(0.95 * H), ry - int(0.022 * H) + box_h)
        draw.rectangle([(int(0.04 * W), top_y), (int(0.96 * W), bot_y)], outline=color, width=3)
        if is_target:
            cx, cy = r["detail_click"] if r["expanded"] else r["expand_click"]
            draw.ellipse([(cx - 16, cy - 16), (cx + 16, cy + 16)], outline="red", width=3)
            draw.line([(cx - 24, cy), (cx + 24, cy)], fill="red", width=2)
            draw.line([(cx, cy - 24), (cx, cy + 24)], fill="red", width=2)
    canvas.save(save_path)

def sanitize_filename(name):
    return re.sub(r'[\\/*?:\x22<>|@\s]', '_', name)

class TeeLogger:
    def __init__(self, *log_paths):
        self.terminal = sys.__stdout__
        self.files = []
        for p in log_paths:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                self.files.append(open(p, "w", encoding="utf-8", buffering=1))
            except Exception:
                pass

    def write(self, message):
        try:
            self.terminal.write(message)
        except Exception:
            pass
        for f in self.files:
            try:
                f.write(message)
                f.flush()
            except Exception:
                pass

    def flush(self):
        try:
            self.terminal.flush()
        except Exception:
            pass
        for f in self.files:
            try:
                f.flush()
            except Exception:
                pass

def main():
    ensure_admin()
    root_log_path = os.path.join(BASE_DIR, "latest_collector.log")
    out_dir = os.path.join(BASE_DIR, "战报导出", f"{TARGET_DATE}_全天战报")
    debug_dir = os.path.join(out_dir, "debug_追踪图")
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(debug_dir, exist_ok=True)
    run_log_path = os.path.join(out_dir, "collector_run.log")
    sys.stdout = sys.stderr = TeeLogger(root_log_path, run_log_path)

    hwnd = find_game()
    if not hwnd:
        print("[ERROR] 未找到游戏窗口 S3Client / EJOY2DX！请先登录打开游戏。")
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

    max_reports = 500
    manifest = []
    manifest_path = os.path.join(out_dir, "manifest.json")

    print("=" * 78)
    print(f"  【三国志·战略版】指定日期（{TARGET_DATE}）内存有序队列全量战报采集器")
    print(f"  🎯 采集规则：自动进【个人战报->交战->只看个人】 | 内存顺序锁定人名 | 全采 {TARGET_DATE}")
    print("=" * 78)
    print(f"窗口尺寸: {W} x {H}, 屏幕原点: ({box['left']}, {box['top']})")
    print(f"战报输出目录: {out_dir}")
    print(f"实时日志文件: {root_log_path}\n")

    ok, init_screen = ensure_on_pvp_list(box, debug_dir)
    if not ok:
        print("\n❌ [停止] 多次尝试自动导航后仍未能进入【个人战报 -> 交战 -> 只看个人】页面，请检查游戏画面。")
        input("按回车退出...")
        return

    print("  ✅ [状态校验] 已通过实时截屏识图确认当前处于：【个人战报】 -> 【交战】栏 -> 【只看个人】！")

    memory_queue = []
    last_start_s = 0
    captured_count = 0
    skipped_newer_count = 0
    older_date_streak = 0
    consecutive_no_scroll = 0
    step_round = 0

    # Comfort threshold for predicted_ry (the row's header Y AFTER any expanded card above it collapses!)
    comfort_max_y = int(0.58 * H)

    while captured_count < max_reports:
        step_round += 1

        ok, cur_screen = ensure_on_pvp_list(box, debug_dir)
        if not ok:
            print("\n❌ [停止] 无法回到【个人战报 -> 交战 -> 只看个人】界面，安全终止。")
            break

        cur_rows = scan_battle_rows(cur_screen)
        if not cur_rows:
            print("\n❌ [停止] 当前【交战】列表未检测到任何战报行！")
            break

        last_start_s, added_cnt = sync_memory_queue(memory_queue, cur_rows, last_start_s)
        if added_cnt > 0:
            new_names = [q["opponent"] for q in memory_queue[-added_cnt:]]
            print(f"\n  🧠 [内存队列入队] 新增 {added_cnt} 个对手进入内存顺序队列: {new_names}")

        pending_queue = [q for q in memory_queue if q["status"] == "PENDING"]
        print(
            f"\n--- [轮播扫描 #{step_round}] 当前屏可见 {len(cur_rows)} 行 | 内存队列总计 {len(memory_queue)} 人 "
            f"(待处理:{len(pending_queue)} | 已截图:{captured_count} | 跳过25日:{skipped_newer_count}) ---"
        )
        for r in cur_rows:
            qid = r.get("qid")
            q_obj = memory_queue[qid - 1] if (isinstance(qid, int) and 1 <= qid <= len(memory_queue)) else None
            st_str = q_obj["status"] if q_obj else "UNKNOWN"
            st_tag = "✅已截图" if st_str == "CAPTURED" else ("⏭️跳过" if st_str.startswith("SKIP") else "⏳待处理")
            exp_tag = "🟢展开" if r["expanded"] else "🔵折叠"
            qid_str = f"{qid:02d}" if isinstance(qid, int) else "??"
            print(f"    [队列#{qid_str}] Y={r['y']:4d} | {st_tag} | {exp_tag} | 对手: 【{r['opponent']}】")

        target_q = pending_queue[0] if pending_queue else None
        target_row = None
        if target_q:
            for r in cur_rows:
                if r.get("qid") == target_q["qid"]:
                    target_row = r
                    break
            if not target_row:
                # Fallback lookup by opponent name: strictly skip any row whose qid is already processed
                # so consecutive battles against the same opponent in an '鏖战' are never conflated!
                for r in cur_rows:
                    rqid = r.get("qid")
                    rq_obj = memory_queue[rqid - 1] if (rqid and 1 <= rqid <= len(memory_queue)) else None
                    if rq_obj and rq_obj["status"] != "PENDING":
                        continue
                    if same_opponent(r["opponent"], target_q["opponent"]):
                        target_row = r
                        r["qid"] = target_q["qid"]
                        break

        # Determine if we need to scroll up to bring `target_q` into the comfort zone
        need_scroll = False
        predicted_ry = target_row["y"] if target_row else 0
        if not target_q:
            need_scroll = True
            print("  📜 当前内存队列中的所有对手均已处理完毕，准备上滑加载下一批新对手...")
        elif not target_row:
            need_scroll = True
            print(f"  📜 下一个待处理对手 [队列#{target_q['qid']:02d} 【{target_q['opponent']}】] 位于当前屏幕下方，上滑加载...")
        else:
            # Within the SAME '鏖战' group (`group_id`), clicking `target_row` collapses any expanded card
            # above it (-0.255*H), shifting `target_row` UP by 0.255*H! Across different '鏖战' banners,
            # the upper group's expanded card does NOT collapse when clicking the lower group.
            has_same_group_expanded_above = any(
                r["expanded"]
                and r.get("group_id") == target_row.get("group_id")
                and r["y"] < target_row["y"] - int(0.03 * H)
                for r in cur_rows
            )
            predicted_ry = target_row["y"] - (
                int(0.255 * H) if (has_same_group_expanded_above and not target_row["expanded"]) else 0
            )
            if predicted_ry > comfort_max_y and consecutive_no_scroll == 0:
                need_scroll = True
                print(
                    f"  📐 [队列#{target_q['qid']:02d} 【{target_q['opponent']}】] 预计展开位置 Y={predicted_ry} 偏下 (> {comfort_max_y})，先上推至舒适区..."
                )

        if need_scroll:
            zero_inertia_scroll_up(box, drag_distance_ratio=0.20)
            after_scroll_screen = capture_box(box)
            delta_y, corr = measure_scroll_shift(cur_screen, after_scroll_screen)
            print(f"  ⚓ [滑动测距] 列表向上平移 ΔY = {delta_y} 像素 (匹配度={corr:.2f})")

            if delta_y < 15:
                consecutive_no_scroll += 1
                print(f"  ⚠️ 列表几乎未移动 (ΔY={delta_y}px)，已到达战报列表底部附近！")
                if not target_q or (not target_row and consecutive_no_scroll >= 2):
                    print("  🏁 内存队列已全部处理完毕且列表已滑到底部，任务完成！")
                    break
            else:
                consecutive_no_scroll = 0
                continue

        if not target_q or not target_row:
            if consecutive_no_scroll >= 2:
                print("  🏁 已滑到底部且无更多可处理战报，结束采集！")
                break
            continue

        target_name = target_q["opponent"]
        active_clean_screen = cur_screen
        print(f"\n▶ [处理内存队列 #{target_q['qid']:02d}] 目标对手: 【{target_name}】 (当前实时定位 Y={target_row['y']}, 展开={target_row['expanded']}, 鏖战组={target_row.get('group_id', 1)})")

        # Step 1: If target_row is collapsed, click directly on the opponent's name to expand it!
        if not target_row["expanded"]:
            ex_x, ex_y = target_row["expand_click"]
            print(f"  -> 条目处于折叠状态，点击实时截图定位的右侧对手人名【{target_name}】 (X={ex_x}, Y={ex_y}) 展开战报...")
            real_click(box["left"] + ex_x, box["top"] + ex_y, hold_sec=0.15)
            time.sleep(0.95)

            # Immediately take a FRESH screenshot after clicking to re-locate the card after accordion drift!
            after_exp_screen = capture_box(box)
            st_after = detect_screen_state(after_exp_screen)["state"]
            if st_after != "REPORT_LIST_PVP":
                diagnose_and_recover_via_screenshot(
                    box, debug_dir, reason=f"点击人名【{target_name}】后画面变为 {st_after}", target_name=target_name
                )
                target_q["tries"] += 1
                if target_q["tries"] >= 3:
                    print(f"  ⚠️ [队列#{target_q['qid']:02d} 【{target_name}】] 连续 3 次展开异常，标记跳过以防死循环。")
                    target_q["status"] = "SKIPPED_ERR"
                continue

            after_rows = scan_battle_rows(after_exp_screen)
            last_start_s, _ = sync_memory_queue(memory_queue, after_rows, last_start_s)

            # Re-locate the newly expanded row on the fresh screenshot (handling accordion Y-drift!)
            shifted_row = None
            for ar in after_rows:
                if ar["expanded"] and ar.get("qid") == target_q["qid"]:
                    shifted_row = ar
                    break
            if not shifted_row:
                for ar in after_rows:
                    ar_qid = ar.get("qid")
                    ar_qobj = memory_queue[ar_qid - 1] if (ar_qid and 1 <= ar_qid <= len(memory_queue)) else None
                    if ar_qobj and ar_qobj["status"] != "PENDING" and ar_qid != target_q["qid"]:
                        continue
                    if ar["expanded"] and same_opponent(ar["opponent"], target_name):
                        shifted_row = ar
                        break
            if not shifted_row:
                # If the expanded card shifted into top_clipped or OCR had noise, pick the expanded card closest to predicted_ry
                exp_candidates = [ar for ar in after_rows if ar["expanded"]]
                if len(exp_candidates) == 1:
                    shifted_row = exp_candidates[0]
                elif len(exp_candidates) > 1:
                    best_cand = min(exp_candidates, key=lambda ar: abs(ar["y"] - predicted_ry))
                    if abs(best_cand["y"] - predicted_ry) <= int(0.18 * H):
                        shifted_row = best_cand

            if not shifted_row or not shifted_row["expanded"]:
                target_q["tries"] += 1
                diagnose_and_recover_via_screenshot(
                    box,
                    debug_dir,
                    reason=f"点击【{target_name}】后未检测到展开 (尝试 {target_q['tries']}/3)",
                    target_name=target_name,
                )
                if target_q["tries"] >= 3:
                    print(f"  ⚠️ [队列#{target_q['qid']:02d} 【{target_name}】] 连续 3 次未能展开，标记跳过。")
                    target_q["status"] = "SKIPPED_ERR"
                continue

            target_row = shifted_row
            active_clean_screen = after_exp_screen
            print(f"  -> 展开成功！重新截图定位【{target_name}】展开后新位置 Y={target_row['y']} (漂移 ΔY={target_row['y'] - ex_y:+d}px)")

        # Step 2: If the expanded card's bottom date line (y + 0.295*H) is below 0.89*H,
        # scroll up unconditionally (expanding a bottom row extends the scrollable list height by 275px!)
        if target_row["y"] + int(0.295 * H) > int(0.89 * H):
            print(f"  📐 展开卡片底部日期 (Y={target_row['y'] + int(0.28 * H)}) 接近底边，上推以完整露出日期与详情点击区...")
            zero_inertia_scroll_up(box, drag_distance_ratio=0.22)
            s2 = capture_box(box)
            r2 = scan_battle_rows(s2)
            last_start_s, _ = sync_memory_queue(memory_queue, r2, last_start_s)
            for ar in r2:
                if ar["expanded"] and (ar.get("qid") == target_q["qid"] or same_opponent(ar["opponent"], target_name)):
                    target_row = ar
                    active_clean_screen = s2
                    break
            else:
                exp_cands = [ar for ar in r2 if ar["expanded"]]
                if len(exp_cands) == 1:
                    target_row = exp_cands[0]
                    active_clean_screen = s2

        # Step 3: Read the battle date & time from the clean un-annotated expanded card!
        b_date, b_time, raw_ts = read_card_datetime(active_clean_screen, target_row["y"])
        print(f"  🕒 [卡片时间戳识别] 日期={b_date or '未读出'} | 时间={b_time or '未读出'} (原文: {raw_ts})")

        if b_date is not None:
            if b_date > TARGET_DATE:
                skipped_newer_count += 1
                target_q["status"] = "SKIPPED_NEWER"
                print(f"  ⏭️ [跳过新战报] [队列#{target_q['qid']:02d} 【{target_name}】] 发生于 {b_date} (晚于 {TARGET_DATE})，状态更新为已跳过！")
                continue
            elif b_date < TARGET_DATE:
                older_date_streak += 1
                target_q["status"] = "SKIPPED_OLDER"
                print(f"  🛑 [遇到历史战报] [队列#{target_q['qid']:02d} 【{target_name}】] 发生于 {b_date} (早于 {TARGET_DATE})！(连续旧战报={older_date_streak})")
                if older_date_streak >= 2:
                    print(f"\n  🏁 连续检测到 {older_date_streak} 场早于 {TARGET_DATE} 的历史战报（{b_date}），确认 {TARGET_DATE} 当天战报已 100% 全部截完！")
                    break
                continue
            else:
                older_date_streak = 0
        else:
            if captured_count == 0 and skipped_newer_count > 0:
                target_q["status"] = "SKIPPED_NEWER"
                print(f"  ⚠️ 尚未进入 {TARGET_DATE} 区间且未读出日期，跳过。")
                continue

        draw_debug_plan(
            active_clean_screen,
            [target_row],
            target_row.get("qid"),
            os.path.join(debug_dir, f"capture_{captured_count + 1:03d}_q{target_q['qid']:02d}_{sanitize_filename(target_name)}.png")
        )

        # Step 4: Click once on the center of the expanded card (X=0.50*W) to enter '战斗回放' detail page.
        # Note: X=0.50*W is the empty middle column on '战斗回放', so even if a click lands late it cannot hit a tactic!
        det_x, det_y = target_row["detail_click"]
        print(f"  🎯 [命中 {TARGET_DATE}] 点击【{target_name}】展开卡中心 (X={det_x}, Y={det_y}) 进入战报详情页...")
        real_click(box["left"] + det_x, box["top"] + det_y, hold_sec=0.15)

        entered = False
        detail_img = None
        for wait_i in range(12):
            time.sleep(0.22)
            chk = capture_box(box)
            st_info = detect_screen_state(chk)
            if st_info["state"] == "POPUP_MODAL":
                # Self-heal if '战法预览' popup was accidentally opened!
                cx, cy = st_info["close_pt"]
                print(f"  🛡️ [弹窗自愈] 进入详情时检测到【{st_info.get('popup_text', '战法预览')}】，实时定位右上角【×】 (X={cx}, Y={cy}) 点击关闭回到详情页...")
                real_click(box["left"] + cx, box["top"] + cy, hold_sec=0.15)
                time.sleep(0.45)
                continue
            if st_info["state"] == "REPORT_DETAIL":
                time.sleep(0.28)
                cand_img = capture_box(box)
                cand_state = detect_screen_state(cand_img)
                if cand_state["state"] == "POPUP_MODAL":
                    cx, cy = cand_state["close_pt"]
                    print(f"  🛡️ [弹窗自愈] 截图前发现【战法预览】遮挡，点击右上角【×】 (X={cx}, Y={cy}) 关闭后重截...")
                    real_click(box["left"] + cx, box["top"] + cy, hold_sec=0.15)
                    time.sleep(0.45)
                    cand_img = capture_box(box)
                entered = True
                detail_img = cand_img
                break

        if entered and detail_img:
            captured_count += 1
            target_q["status"] = "CAPTURED"
            safe_name = sanitize_filename(target_name)
            safe_time = (b_time or "000000").replace(":", "")
            fname = f"{captured_count:03d}_{b_date or TARGET_DATE}_{safe_time}_{safe_name}.png"
            fpath = os.path.join(out_dir, fname)
            detail_img.save(fpath)

            # Perform immediate full-screen OCR on the detail screenshot for backend/Claude consumption
            det_lines = [l.text.replace(" ", "") for l in ocr_pil(detail_img)]
            full_det_text = " | ".join(det_lines)
            merit_m = re.search(r"战功[:：]?(\d+)|获得战功[:：]?(\d+)", full_det_text)
            merit_val = int(merit_m.group(1) or merit_m.group(2)) if merit_m else 0
            troop_pairs = re.findall(r"(\d{2,5})[/／](\d{3,5})", full_det_text)
            our_troops = f"{troop_pairs[0][0]}/{troop_pairs[0][1]}" if len(troop_pairs) >= 1 else ""
            enemy_troops = f"{troop_pairs[1][0]}/{troop_pairs[1][1]}" if len(troop_pairs) >= 2 else ""

            print(
                f"  -> ✅ [状态更新: CAPTURED] 成功保存并识图第 {captured_count} 张 ({TARGET_DATE}) 战报: {fname} "
                f"(我方兵力={our_troops or '已识图'} | 敌方兵力={enemy_troops or '已识图'} | OCR行数={len(det_lines)})"
            )

            manifest.append({
                "index": captured_count,
                "queueId": target_q["qid"],
                "filename": fname,
                "filepath": fpath,
                "battleDate": b_date or TARGET_DATE,
                "battleTime": b_time or "",
                "opponent": target_name,
                "hasEnemyAlliance": target_row["has_enemy_alliance"],
                "ourTroops": our_troops,
                "enemyTroops": enemy_troops,
                "merit": merit_val,
                "ocrLines": det_lines,
                "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            })
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, ensure_ascii=False, indent=2)

            csv_path = os.path.join(out_dir, f"战报汇总_{TARGET_DATE}.csv")
            csv_exists = os.path.exists(csv_path)
            with open(csv_path, "a", encoding="utf-8-sig") as cf:
                if not csv_exists:
                    cf.write("序号,队列ID,战斗日期,战斗时间,对手名,我方兵力,敌方兵力,战功,文件名\n")
                cf.write(
                    f"{captured_count},{target_q['qid']},{b_date or TARGET_DATE},{b_time or ''},"
                    f"{target_name},{our_troops},{enemy_troops},{merit_val},{fname}\n"
                )

            ensure_on_pvp_list(box, debug_dir)
            time.sleep(0.4)
        else:
            target_q["tries"] += 1
            diagnose_and_recover_via_screenshot(
                box,
                debug_dir,
                reason=f"点击展开卡中心后未进入详情页 (尝试 {target_q['tries']}/3)",
                target_name=target_name,
            )
            if target_q["tries"] >= 3:
                print(f"  -> ❌ [队列#{target_q['qid']:02d} 【{target_name}】] 连续 3 次无法进入详情页，标记异常跳过。")
                target_q["status"] = "SKIPPED_ERR"

    print("\n" + "=" * 78)
    print(f"🎉 【{TARGET_DATE}】全天战报采集圆满结束！")
    print(f"📊 统计：内存队列共记录 {len(memory_queue)} 名对手 | 跳过 10-25 新战报 {skipped_newer_count} 场 | 成功截取 {TARGET_DATE} 战报 {captured_count} 场！")
    print(f"📂 战报与清单目录: {out_dir}")
    print("=" * 78)
    input("\n按【回车键】关闭窗口...")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        err_msg = f"\n[FATAL ERROR] 采集器运行发生未捕获异常: {e}\n" + traceback.format_exc()
        print(err_msg)
        try:
            with open(os.path.join(BASE_DIR, "latest_collector.log"), "a", encoding="utf-8") as ef:
                ef.write(err_msg)
        except Exception:
            pass
        input("\n⚠️ 发生异常已记录至 latest_collector.log，按【回车键】关闭窗口...")
