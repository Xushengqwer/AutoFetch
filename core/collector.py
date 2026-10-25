import os
import time
import json
import random
import re
import asyncio
import threading
from datetime import datetime
import pyautogui
import numpy as np
import cv2
from PIL import Image

pyautogui.FAILSAFE = True  # Move mouse to screen corner to emergency stop

def cv_imread(path):
    if not os.path.exists(path):
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)

def compute_image_hash(pil_img):
    """Compute difference hash (dHash) to uniquely identify battle reports."""
    try:
        small = pil_img.resize((17, 16)).convert('L')
        pixels = list(small.getdata())
        diff = []
        for row in range(16):
            for col in range(16):
                p1 = pixels[row * 17 + col]
                p2 = pixels[row * 17 + col + 1]
                diff.append('1' if p1 > p2 else '0')
        return hex(int(''.join(diff), 2))[2:]
    except Exception:
        return None

def hamming_dist(h1, h2):
    """Compute Hamming distance between two hex dHash strings."""
    if not h1 or not h2:
        return 999
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count('1')
    except Exception:
        return 999

from core.game_window import GameWindow
from core.ocr_engine import OCREngine

class BattleCollector:
    def __init__(self, config=None, log_cb=None, status_cb=None):
        self.config = config or {}
        self.log_cb = log_cb or print
        self.status_cb = status_cb or (lambda s: None)
        self.game_win = GameWindow()
        self.ocr = OCREngine()
        self.is_running = False
        self.stop_requested = False
        self.collected_count = 0

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.templates_dir = os.path.join(base_dir, "templates")

    def log(self, msg):
        self.log_cb(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def find_template_match(self, screen_img, template_name, threshold=0.68, base_width=1722):
        """Find center coordinates of a template in screen_img with dynamic scaling."""
        if screen_img is None:
            return None

        t_path = os.path.join(self.templates_dir, template_name)
        tmpl = cv_imread(t_path)
        if tmpl is None:
            return None

        if hasattr(screen_img, 'convert'):
            screen_bgr = cv2.cvtColor(np.array(screen_img), cv2.COLOR_RGB2BGR)
        elif isinstance(screen_img, np.ndarray):
            screen_bgr = screen_img
        else:
            return None

        sw = screen_bgr.shape[1]
        sh = screen_bgr.shape[0]
        if sw <= 10 or sh <= 10:
            return None

        scale = sw / float(base_width)
        if abs(scale - 1.0) > 0.05 and scale > 0.15:
            new_w = max(8, int(tmpl.shape[1] * scale))
            new_h = max(8, int(tmpl.shape[0] * scale))
            tmpl_scaled = cv2.resize(tmpl, (new_w, new_h), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
        else:
            tmpl_scaled = tmpl

        if sh < tmpl_scaled.shape[0] or sw < tmpl_scaled.shape[1]:
            return None

        try:
            res = cv2.matchTemplate(screen_bgr, tmpl_scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val >= threshold:
                th, tw = tmpl_scaled.shape[:2]
                cx = max_loc[0] + tw // 2
                cy = max_loc[1] + th // 2
                return (cx, cy, max_val)
        except Exception:
            return None
        return None

    def find_collapsed_rows(self, screen_bgr):
        """
        Find Y coordinates (relative to window top) of all collapsed battle rows on screen.
        Uses down-arrow ('arrow_real.png') matching in the right margin.
        Returns sorted list [y1, y2, ...] from top to bottom.
        """
        if screen_bgr is None:
            return []

        tmpl_path = os.path.join(self.templates_dir, "arrow_real.png")
        tmpl = cv_imread(tmpl_path)
        if tmpl is None:
            return []

        h, w = screen_bgr.shape[:2]
        crop = screen_bgr[int(0.15 * h):int(0.92 * h), int(0.85 * w):w]
        ch, cw = crop.shape[:2]
        th, tw = tmpl.shape[:2]
        base_scale = w / 1024.0
        matches_all = []

        for s_mult in [0.75, 0.85, 1.0, 1.15, 1.3]:
            s = base_scale * s_mult
            nw = int(tw * s)
            nh = int(th * s)
            if nw >= cw or nh >= ch or nw < 5 or nh < 5:
                continue
            ts = cv2.resize(tmpl, (nw, nh), interpolation=cv2.INTER_LINEAR)
            res = cv2.matchTemplate(crop, ts, cv2.TM_CCOEFF_NORMED)
            locs = np.where(res >= 0.65)
            for pt in zip(*locs[::-1]):
                real_y = int(0.15 * h) + pt[1] + nh // 2
                matches_all.append((real_y, res[pt[1], pt[0]]))

        clusters = []
        for y, score in sorted(matches_all, key=lambda m: -m[1]):
            if not any(abs(y - cy) < int(0.035 * h) for cy in clusters):
                clusters.append(int(y))
        clusters.sort()
        return clusters

    def smooth_click(self, screen_x, screen_y, jitter=True, hold_sec=0.14):
        """Move smoothly and execute frame-holding click for Direct3D games."""
        if jitter:
            screen_x += random.randint(-2, 2)
            screen_y += random.randint(-2, 2)
        pyautogui.moveTo(screen_x, screen_y, duration=random.uniform(0.15, 0.22))
        time.sleep(random.uniform(0.04, 0.06))
        pyautogui.mouseDown(screen_x, screen_y, button='left')
        time.sleep(hold_sec)
        pyautogui.mouseUp(screen_x, screen_y, button='left')
        time.sleep(random.uniform(0.08, 0.12))

    def scroll_list_down(self, box, distance_ratio=0.20):
        """
        Controlled gentle upward drag with zero-fling pause.
        Moves list upwards gently by ~20% of screen height to reveal the next batch.
        """
        cx = box['left'] + int(box['width'] * 0.30)
        start_y = box['top'] + int(box['height'] * 0.65)
        end_y = box['top'] + int(box['height'] * (0.65 - distance_ratio))

        pyautogui.moveTo(cx, start_y, duration=0.18)
        time.sleep(0.06)
        pyautogui.mouseDown(cx, start_y, button='left')
        time.sleep(0.12)
        pyautogui.moveTo(cx, end_y, duration=0.42)
        time.sleep(0.20)  # Essential pause: completely eliminates fling velocity!
        pyautogui.mouseUp(cx, end_y, button='left')
        time.sleep(0.80)

    def is_in_detail_view(self, screen_img):
        """Check if currently inside the battle detail view."""
        if screen_img is None:
            return False
        # Check template match for 'tag_huifang.png'
        if self.find_template_match(screen_img, "tag_huifang.png", threshold=0.68):
            return True
        info = self.ocr.extract_battle_info(screen_img)
        clean = "".join([l.replace(' ', '') for l in info.get("lines", [])])
        if "战斗回放" in clean or "回放" in clean:
            return True
        has_back = bool(self.find_template_match(screen_img, "btn_back_real.png", threshold=0.68, base_width=1722))
        has_read_btn = "全部标记已读" in clean
        return has_back and not has_read_btn

    def is_in_report_list(self, screen_img):
        """Check if currently on the personal battle report list page using multiple robust indicators."""
        if screen_img is None:
            return False

        # 1. OCR text detection (immune to DPI/resolution changes)
        info = self.ocr.extract_battle_info(screen_img)
        clean_text = "".join([l.replace(' ', '') for l in info.get("lines", [])])
        if any(kw in clean_text for kw in ["个人战报", "全部标记已读", "交战", "只看个人", "开拓"]):
            return True

        # 2. Check if battle dates exist on screen (e.g. 2026年10月22日)
        if re.search(r'(?:20)?2\d\D+\d{1,2}\D+\d{1,2}', clean_text):
            return True

        # 3. Check if collapsed battle arrows exist on screen
        cv_img = cv2.cvtColor(np.array(screen_img), cv2.COLOR_RGB2BGR)
        if len(self.find_collapsed_rows(cv_img)) > 0:
            return True

        # 4. Template matching with base_width=1722
        if self.find_template_match(screen_img, "btn_all_read.png", threshold=0.60, base_width=1722):
            return True
        if self.find_template_match(screen_img, "tag_gerenzhanbao.png", threshold=0.60, base_width=1722):
            return True
        return False

    def check_and_dismiss_exit_modal(self, screen_img, box):
        """
        Check if the game's 'Exit Game' modal dialog accidentally appeared.
        If yes, click [取消] to dismiss it immediately so we never get stuck!
        """
        if screen_img is None:
            return False
        info = self.ocr.extract_battle_info(screen_img)
        lines = info.get("lines", [])
        has_exit_prompt = any("退出" in l or "确认退出" in l or "是否" in l for l in lines)
        has_cancel = any("取消" in l for l in lines)
        if has_exit_prompt or has_cancel:
            self.log("⚠️ 检测到【退出游戏】确认弹窗，正在自动点击【取消】恢复...")
            cancel_x = box['left'] + int(box['width'] * 0.42)
            cancel_y = box['top'] + int(box['height'] * 0.60)
            self.smooth_click(cancel_x, cancel_y)
            time.sleep(0.8)
            return True
        return False

    def check_and_recover_from_map(self, screen_img, box):
        """
        If the screen accidentally jumped to the Big World Map (e.g. clicked location link),
        detect it and immediately click bottom-left 【战报】 icon to return!
        """
        if screen_img is None:
            return False
        info = self.ocr.extract_battle_info(screen_img)
        clean = "".join([l.replace(' ', '') for l in info.get("lines", [])])
        is_map = any(kw in clean for kw in ["武将", "战法", "政厅", "招募", "任务", "更多"])
        is_report = any(kw in clean for kw in ["个人战报", "全部标记已读", "交战", "只看个人"])
        if is_map and not is_report:
            self.log("🗺️ 检测到误入大地图，正在自动点击左下角【战报】切回...")
            # Click bottom-left 【战报】 icon
            self.smooth_click(box['left'] + int(box['width'] * 0.055), box['top'] + int(box['height'] * 0.815))
            time.sleep(1.2)
            # Click 【交战】 tab
            self.smooth_click(box['left'] + int(box['width'] * 0.265), box['top'] + int(box['height'] * 0.160))
            time.sleep(0.8)
            return True
        return False

    def find_expanded_card_click_point(self, screen_img, box):
        """
        Intelligently find the safe click point on the expanded card.
        CRITICAL OBSTACLE AVOIDANCE:
        - NEVER click in the center (x=0.50*W) because that is where location links
          (e.g. '长社大营(1318,913)', '4级木材(1317,921)') are located!
        - ALWAYS click on the left-side player banner area (x = 0.25 * W)!
        - Y coordinate is determined dynamically from the OCR date position!
        """
        safe_x = box['left'] + int(box['width'] * 0.25)

        # 1. Use dynamic OCR date Y position
        info = self.ocr.extract_battle_info(screen_img)
        date_y = info.get("date_y")
        if date_y is not None and date_y > 100:
            # Click ~8% of height above date (falls safely on player name banner / hero card)
            card_y = box['top'] + int(date_y) - int(0.08 * box['height'])
            return (safe_x, card_y)

        # 2. Try template matching for outcome words
        for tmpl_name in ["word_bai.png", "word_sheng.png", "word_ping_real.png"]:
            match = self.find_template_match(screen_img, tmpl_name, threshold=0.68)
            if match:
                return (safe_x, box['top'] + match[1])

        # 3. Dynamic fallback: middle area
        return (safe_x, box['top'] + int(box['height'] * 0.50))

    def detect_outcome(self, screen_img):
        """Detect battle outcome string ('胜', '负', '平') from image."""
        if self.find_template_match(screen_img, "word_sheng.png", threshold=0.70):
            return "胜"
        if self.find_template_match(screen_img, "word_bai.png", threshold=0.70):
            return "败"
        if self.find_template_match(screen_img, "word_ping_real.png", threshold=0.70) or self.find_template_match(screen_img, "word_ping.png", threshold=0.70):
            return "平"
        info = self.ocr.extract_battle_info(screen_img)
        lines = "".join(info.get("lines", []))
        if "胜" in lines:
            return "胜"
        if "败" in lines or "负" in lines:
            return "败"
        if "平" in lines:
            return "平"
        return "战报"

    def _preload_existing_hashes(self, save_root):
        """Pre-load existing battle timestamps and hashes to avoid re-capturing."""
        seen_slugs = set()
        seen_hashes = set()
        if not os.path.exists(save_root):
            return seen_slugs, seen_hashes

        for root, _, files in os.walk(save_root):
            for f in files:
                if f.endswith('.png') and '_' in f:
                    slug_part = f.split('_')[0] + "_" + f.split('_')[1] if len(f.split('_')) >= 2 else f
                    seen_slugs.add(slug_part)
                    full_p = os.path.join(root, f)
                    try:
                        im = Image.open(full_p)
                        h = compute_image_hash(im)
                        if h:
                            seen_hashes.add(h)
                    except Exception:
                        pass
        return seen_slugs, seen_hashes

    def run_collection(self, max_count=100, save_root="战报导出", only_today=False):
        self.is_running = True
        self.stop_requested = False
        self.status_cb("正在检测游戏窗口...")
        self.log("=== 开始执行战报自动化收集 ===")

        # Wait a moment for the tool window to minimize itself
        time.sleep(0.6)

        hwnd = self.game_win.find_window()
        if not hwnd:
            self.log("❌ 未找到《三国志·战略版》运行窗口！请确认已打开 PC 桌面版。")
            self.status_cb("未找到游戏窗口")
            self.is_running = False
            return

        self.log("✅ 成功锁定三战游戏窗口，正在激活...")
        self.game_win.activate()
        time.sleep(0.5)

        # 1. Automatic Window Normalization check
        box = self.game_win.get_client_box()
        if not box or box['width'] <= 100 or box['height'] <= 100:
            self.log("❌ 无法获取游戏画面大小（当前为 0x0）！")
            self.log("💡 请在任务栏点击《三国志·战略版》，将其还原显示在桌面上（不要最小化）。")
            self.status_cb("请先还原游戏窗口")
            self.is_running = False
            return

        w, h = box['width'], box['height']
        aspect = w / float(h)
        if w >= 1100 and h >= 600 and 1.60 <= aspect <= 1.85:
            self.log(f"📐 游戏画面分辨率良好 ({w} x {h})，保持当前尺寸。")
        else:
            self.log("📐 正在规范化游戏窗口至标准尺寸 (1280 x 720)，抹平不同屏幕与缩放差异...")
            self.game_win.normalize_window(target_client_w=1280, target_client_h=720)
            time.sleep(1.0)
            box = self.game_win.get_client_box()

        self.log(f"📐 游戏画面校准完成: 宽度 {box['width']} x 高度 {box['height']}")

        # Ensure we are in Battle Reports List
        screen = self.game_win.capture_client()
        self.check_and_dismiss_exit_modal(screen, box)
        self.check_and_recover_from_map(screen, box)

        if not self.is_in_report_list(screen):
            self.log("🔍 当前未检测到战报列表，尝试点击主界面【战报】入口...")
            click_x = box['left'] + int(box['width'] * 0.055)
            click_y = box['top'] + int(box['height'] * 0.815)
            self.smooth_click(click_x, click_y)
            time.sleep(1.5)
            screen = self.game_win.capture_client()

        # Switch to 【交战】 tab
        self.log("🔄 确认切换到【交战】PVP战报标签页...")
        tab_click_x = box['left'] + int(box['width'] * 0.265)
        tab_click_y = box['top'] + int(box['height'] * 0.160)
        self.smooth_click(tab_click_x, tab_click_y)
        time.sleep(0.8)

        collected_count = 0
        self.collected_count = 0
        consecutive_duplicates = 0
        consecutive_wall_hits = 0
        today_str = datetime.now().strftime("%Y-%m-%d")

        # Pre-load already saved battles so we never re-capture duplicates
        seen_slugs, seen_hashes = self._preload_existing_hashes(save_root)
        if seen_slugs:
            self.log(f"📂 已自动加载历史库中 {len(seen_slugs)} 条已保存记录，避免重复录入。")

        self.status_cb("正在收集中...")
        seen_page_row_keys = set()

        while collected_count < max_count and not self.stop_requested:
            screen = self.game_win.capture_client()
            if screen is None:
                break

            # SAFETY GUARD 1: Are we still stuck in the Detail View?
            if self.is_in_detail_view(screen):
                self.log("⚠️ 检测到当前仍停留在详情页，正在安全返回战报列表...")
                self._return_to_list_closed_loop(box)
                screen = self.game_win.capture_client()

            # SAFETY GUARD 2: Auto-dismiss exit modal if present
            if self.check_and_dismiss_exit_modal(screen, box):
                screen = self.game_win.capture_client()

            # SAFETY GUARD 3: Auto-recover from world map if accidentally opened
            if self.check_and_recover_from_map(screen, box):
                screen = self.game_win.capture_client()

            # STEP 1: Check if an expanded card is ALREADY visible on screen!
            # An expanded card has the date/time string and the huge outcome (平/胜/败).
            exp_card = self.ocr.find_expanded_card(screen)
            action_taken = False

            if exp_card and exp_card["slug"] not in seen_slugs:
                # If date is from earlier and user only wants today's reports:
                if only_today and exp_card["date"] != today_str:
                    self.log(f"📅 检测到历史战报日期 {exp_card['date']}，今日战报已全部采集完毕！")
                    self.stop_requested = True
                    break

                # If the expanded card is cut off near the bottom edge:
                if exp_card["date_y"] > int(0.88 * box['height']):
                    self.log("📐 战报卡片位于底部边缘，轻微向上推移以完全展现...")
                    self.scroll_list_down(box, distance_ratio=0.15)
                    screen = self.game_win.capture_client()
                    exp_card = self.ocr.find_expanded_card(screen)

                if exp_card and exp_card["slug"] not in seen_slugs:
                    self.log(f"🎯 正在点击中央【平/胜/败】大字进入全屏详情 ({exp_card['slug']})...")
                    click_x = box['left'] + exp_card["click_x"]
                    click_y = box['top'] + exp_card["click_y"]
                    self.smooth_click(click_x, click_y, hold_sec=0.15)

                    # Poll for Detail View
                    in_detail = False
                    for _ in range(12):
                        time.sleep(0.25)
                        detail_screen = self.game_win.capture_client()
                        if self.is_in_detail_view(detail_screen):
                            in_detail = True
                            break

                    if in_detail:
                        time.sleep(0.5)
                        final_detail = self.game_win.capture_client()
                        outcome = self.detect_outcome(final_detail)
                        success, date_str, saved_slug = self._process_and_save_detail(
                            final_detail, box, save_root, today_str, only_today, seen_slugs, seen_hashes,
                            collected_count + 1, outcome, true_slug=exp_card["slug"], true_date=exp_card["date"]
                        )
                        if success:
                            collected_count += 1
                            self.collected_count = collected_count
                        else:
                            self.log("⏩ 该战报已收录过（重复已过滤）")

                        self._return_to_list_closed_loop(box)
                        consecutive_wall_hits = 0
                        action_taken = True
                    else:
                        self.log(f"⚠️ 点击未进入详情页，记录并跳过: {exp_card['slug']}")
                        seen_slugs.add(exp_card["slug"])

            # STEP 2: If no new expanded card, find uncollected multi-battle round rows (【第X场】)
            if not action_taken and not self.stop_requested:
                round_rows = self.ocr.find_all_round_rows(screen)
                unprocessed_rows = [r for r in round_rows if f"{r['y'] // 25}" not in seen_page_row_keys]

                if unprocessed_rows:
                    row = unprocessed_rows[0]
                    row_key = f"{row['y'] // 25}"
                    self.log(f"🔎 正在点击展开连战条目: {row['text']} (第 {row['round_num']} 场)...")
                    click_x = box['left'] + row['click_x']
                    click_y = box['top'] + row['click_y']
                    self.smooth_click(click_x, click_y, hold_sec=0.15)
                    time.sleep(0.7)  # Wait for accordion to expand
                    seen_page_row_keys.add(row_key)
                    action_taken = True

            # STEP 3: Fallback for single battle cards without round markers
            if not action_taken and not self.stop_requested:
                cards = self.ocr.find_all_cards(screen)
                uncollected_cards = [c for c in cards if c["slug"] not in seen_slugs]
                if uncollected_cards:
                    card = uncollected_cards[0]
                    self.log(f"🔎 正在点击单场战报: {card['text'][:12]}...")
                    click_x = box['left'] + int(box['width'] * 0.35)
                    click_y = box['top'] + card["click_y"]
                    self.smooth_click(click_x, click_y, hold_sec=0.15)
                    time.sleep(0.7)
                    action_taken = True

            # STEP 4: If all items on current screen are processed, scroll gently to load next batch!
            if not action_taken and not self.stop_requested:
                self.log("📜 当前画面战报已全部采集完毕，轻柔上滑加载下一批...")
                seen_page_row_keys.clear()
                hash_before = compute_image_hash(screen)
                self.scroll_list_down(box, distance_ratio=0.22)
                time.sleep(0.8)
                after_screen = self.game_win.capture_client()
                hash_after = compute_image_hash(after_screen)

                if hamming_dist(hash_before, hash_after) <= 3:
                    consecutive_wall_hits += 1
                    if consecutive_wall_hits >= 2:
                        self.log("🏁 战报列表已滚动到底部，全部战报已收集完毕！")
                        break
                else:
                    consecutive_wall_hits = 0

        self.is_running = False
        if self.stop_requested:
            self.log("🛑 收集已被用户手动中止。")
            self.status_cb("已停止")
        else:
            self.log(f"🎉 收集完成！本次共成功采集 {collected_count} 份不重复的新战报。")
            self.status_cb("收集完成")

    def _process_and_save_detail(self, detail_screen, box, save_root, today_str, only_today, seen_slugs, seen_hashes, seq_num, outcome="战报", true_slug=None, true_date=None):
        """Extract info, deduplicate via visual hash and slug, save screenshot. Returns (success, date_str, slug)."""
        # 1. Visual Hash Deduplication
        img_hash = compute_image_hash(detail_screen)
        if img_hash and img_hash in seen_hashes:
            return (False, None, None)

        # 2. Extract OCR info or use true slug passed from list view
        if true_slug and true_date:
            date_str = true_date
            ts_slug = true_slug
            time_str = true_slug.split('_')[-1] if '_' in true_slug else ""
        else:
            info = self.ocr.extract_battle_info(detail_screen)
            date_str = info["date"]
            ts_slug = info["timestamp_slug"]
            time_str = info["time"]

        if ts_slug in seen_slugs:
            return (False, date_str, ts_slug)

        if only_today and date_str != today_str:
            return (False, date_str, ts_slug)

        if img_hash:
            seen_hashes.add(img_hash)
        seen_slugs.add(ts_slug)

        # 3. Save screenshot grouped by date with clean readable naming
        date_folder = os.path.join(save_root, date_str)
        os.makedirs(date_folder, exist_ok=True)
        filename = f"{ts_slug}_{outcome}_战报_{seq_num}.png"
        file_path = os.path.join(date_folder, filename)
        detail_screen.save(file_path)

        self.log(f"📸 [{seq_num}] 成功保存战报: 日期 {date_str} 时间 {time_str} 【{outcome}】")
        self.log(f"   📁 存储至: {date_str}/{filename}")

        # 4. Record to CSV summary ledger
        try:
            csv_path = os.path.join(save_root, "战报汇总.csv")
            file_exists = os.path.exists(csv_path)
            with open(csv_path, "a", encoding="utf-8-sig") as f:
                if not file_exists:
                    f.write("序号,日期,时间,胜负,文件名\n")
                f.write(f"{seq_num},{date_str},{time_str},{outcome},{filename}\n")
        except Exception:
            pass

        self._try_upload(file_path, {"date": date_str, "time": time_str, "timestamp_slug": ts_slug})
        return (True, date_str, ts_slug)

    def _return_to_list_closed_loop(self, box):
        """
        Closed-loop return from detail view to report list.
        Uses OCR to find the exact top-right Return button first (immune to window scale/DPI!).
        Fallbacks to template matching 'btn_back_real.png' and coordinate ratio.
        """
        for attempt in range(4):
            cur_screen = self.game_win.capture_client()
            if not self.is_in_detail_view(cur_screen):
                time.sleep(0.3)
                return True

            # 1. OCR exact detection of '返回' button
            back_pt = self.ocr.find_return_button(cur_screen)
            if back_pt:
                back_x = box['left'] + back_pt[0]
                back_y = box['top'] + back_pt[1]
            else:
                # 2. Match golden return button in top right
                match = self.find_template_match(cur_screen, "btn_back_real.png", threshold=0.65, base_width=1722)
                if match:
                    back_x = box['left'] + match[0]
                    back_y = box['top'] + match[1]
                else:
                    back_x = box['left'] + int(box['width'] * 0.94)
                    back_y = box['top'] + int(box['height'] * 0.04)

            self.smooth_click(back_x, back_y)

            # Poll for detail view to disappear
            for _ in range(8):
                time.sleep(0.2)
                chk_screen = self.game_win.capture_client()
                if not self.is_in_detail_view(chk_screen) and self.is_in_report_list(chk_screen):
                    time.sleep(0.3)
                    return True

        return False

    def _try_upload(self, file_path, info):
        server_url = self.config.get("server_url", "").strip()
        if not server_url or not self.config.get("auto_upload", False):
            return
        self.log(f"🚀 [云端同步预留] 已准备好上传: {os.path.basename(file_path)}")

    def stop(self):
        self.stop_requested = True
