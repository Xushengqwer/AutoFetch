import asyncio
import re
from datetime import datetime
from PIL import Image

try:
    import winocr
except ImportError:
    winocr = None

class OCREngine:
    def __init__(self, lang="zh-Hans-CN"):
        self.lang = lang

    def extract_battle_info(self, pil_image: Image.Image):
        """
        Extract date/time and other battle identifiers from the image.
        Returns a dict:
            {
                "date": "2026-10-22",
                "time": "14:39:44",
                "timestamp_slug": "2026-10-22_143944",
                "lines": [...]
            }
        """
        if not winocr or pil_image is None:
            now = datetime.now()
            return {
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "timestamp_slug": now.strftime("%Y-%m-%d_%H%M%S"),
                "lines": []
            }

        try:
            async def _runner():
                return await winocr.recognize_pil(pil_image, lang=self.lang)
            res = asyncio.run(_runner())
            lines = [l.text for l in res.lines]
        except Exception as e:
            lines = []

        # Find date pattern: YYYY年MM月DD日 HH:MM:SS or YY年MM月DD日 HH:MM:SS
        date_result = None
        for raw_text in lines:
            text = raw_text.replace(' ', '')
            m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', text)
            if not m:
                m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})', text)
            if m:
                yr_2d, mon, d, h, minute, s = m.groups()
                if 1 <= int(mon) <= 12 and 1 <= int(d) <= 31 and 0 <= int(h) <= 23 and 0 <= int(minute) <= 59 and 0 <= int(s) <= 59:
                    date_str = f"20{yr_2d}-{int(mon):02d}-{int(d):02d}"
                    time_str = f"{int(h):02d}:{int(minute):02d}:{int(s):02d}"
                    # Find Y coordinate of this line if available
                    date_y = None
                    try:
                        for l in res.lines:
                            if l.text.replace(' ', '') == text and len(l.words) > 0:
                                date_y = l.words[0].bounding_rect.y
                                break
                    except Exception:
                        pass
                    date_result = {
                        "date": date_str,
                        "time": time_str,
                        "timestamp_slug": f"{date_str}_{int(h):02d}{int(minute):02d}{int(s):02d}",
                        "date_y": date_y,
                        "lines": lines,
                        "is_real_date": True
                    }
                    break

        if not date_result:
            now = datetime.now()
            date_result = {
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "timestamp_slug": now.strftime("%Y-%m-%d_%H%M%S"),
                "date_y": None,
                "lines": lines,
                "is_real_date": False
            }

        return date_result

    def find_all_cards(self, pil_image: Image.Image):
        """
        Extract ALL battle cards present on the list view screen.
        Returns a list of dicts sorted from TOP to BOTTOM by Y coordinate:
        [
            {
                "y": 295,
                "click_y": 248,
                "date": "2026-10-22",
                "time": "15:38:19",
                "timestamp_slug": "2026-10-22_153819",
                "text": "..."
            },
            ...
        ]
        """
        if not winocr or pil_image is None:
            return []

        w, h = pil_image.size
        try:
            async def _runner():
                return await winocr.recognize_pil(pil_image, lang=self.lang)
            res = asyncio.run(_runner())
        except Exception:
            return []

        cards = []
        for line in res.lines:
            text = line.text.replace(' ', '')
            m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', text)
            if not m:
                m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})', text)
            if m:
                yr_2d, mon, d, hr, minute, sec = m.groups()
                if 1 <= int(mon) <= 12 and 1 <= int(d) <= 31 and 0 <= int(hr) <= 23 and 0 <= int(minute) <= 59 and 0 <= int(sec) <= 59:
                    date_str = f"20{yr_2d}-{int(mon):02d}-{int(d):02d}"
                    time_str = f"{int(hr):02d}:{int(minute):02d}:{int(sec):02d}"
                    slug = f"{date_str}_{int(hr):02d}{int(minute):02d}{int(sec):02d}"
                    y = int(line.words[0].bounding_rect.y) if line.words else 0
                    # Safe click point on the card banner is above the date line
                    click_y = max(int(0.18 * h), y - int(0.08 * h))
                    cards.append({
                        "y": y,
                        "click_y": click_y,
                        "slug": slug,
                        "date": date_str,
                        "time": time_str,
                        "text": text
                    })

        # Sort strictly from TOP to BOTTOM (ascending Y)
        cards.sort(key=lambda c: c["y"])
        return cards

    def find_all_round_rows(self, pil_image: Image.Image):
        """
        Find all multi-battle round rows matching 【第X场 胜/平/败】 across any screen resolution.
        Returns list of dicts sorted strictly from TOP to BOTTOM:
        [
            {'y': 836, 'click_x': 672, 'click_y': 836, 'text': '【第三场平】', 'round_num': '三'},
            ...
        ]
        """
        if not winocr or pil_image is None:
            return []

        w, h = pil_image.size
        try:
            async def _runner():
                return await winocr.recognize_pil(pil_image, lang=self.lang)
            res = asyncio.run(_runner())
        except Exception:
            return []

        rows = []
        for l in res.lines:
            t = l.text.replace(' ', '')
            m = re.search(r'[【〔\[(]?第([一二三四五六七八九十\d]+)场\s*(胜|败|平)?[】〕\])]?', t)
            if m and l.words:
                round_num = m.group(1)
                outcome = m.group(2) if m.group(2) else ""
                w0 = l.words[0]
                cy = int(w0.bounding_rect.y + w0.bounding_rect.height / 2)
                # Ensure it's in the list vertical range (15% to 92% of screen)
                if int(0.15 * h) < cy < int(0.95 * h):
                    rows.append({
                        'y': cy,
                        'click_x': int(w * 0.35),
                        'click_y': cy,
                        'text': t,
                        'round_num': round_num,
                        'outcome': outcome
                    })

        # Deduplicate rows within 30 pixels
        unique_rows = []
        rows.sort(key=lambda r: r['y'])
        for r in rows:
            if not any(abs(r['y'] - u['y']) < int(0.035 * h) for u in unique_rows):
                unique_rows.append(r)
        return unique_rows

    def find_expanded_card(self, pil_image: Image.Image):
        """
        Detect if an expanded battle card is currently visible on screen.
        An expanded card ALWAYS displays the full date & time pattern below the huge outcome word:
        '2026年10月24日 15:42:53'
        Returns dict or None:
        {
            'slug': '2026-10-24_154253',
            'date': '2026-10-24',
            'time': '15:42:53',
            'click_x': center_x,
            'click_y': outcome_center_y, # center of big '平/胜/败'
            'date_y': date_y
        }
        """
        if not winocr or pil_image is None:
            return None

        w, h = pil_image.size
        try:
            async def _runner():
                return await winocr.recognize_pil(pil_image, lang=self.lang)
            res = asyncio.run(_runner())
        except Exception:
            return None

        for l in res.lines:
            t = l.text.replace(' ', '')
            m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})[:：](\d{1,2})[:：](\d{1,2})', t)
            if not m:
                m = re.search(r'(?:20)?(2\d)\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})', t)
            if m and l.words:
                yr_2d, mon, d, hr, minute, sec = m.groups()
                if 1 <= int(mon) <= 12 and 1 <= int(d) <= 31 and 0 <= int(hr) <= 23 and 0 <= int(minute) <= 59 and 0 <= int(sec) <= 59:
                    date_str = f"20{yr_2d}-{int(mon):02d}-{int(d):02d}"
                    time_str = f"{int(hr):02d}:{int(minute):02d}:{int(sec):02d}"
                    slug = f"{date_str}_{int(hr):02d}{int(minute):02d}{int(sec):02d}"
                    w0 = l.words[0]
                    date_y = int(w0.bounding_rect.y + w0.bounding_rect.height / 2)
                    
                    # The big outcome character (平/胜/败) is horizontally centered at 0.50*w,
                    # and vertically located ~11% to 13% of screen height above the date string.
                    outcome_y = max(int(0.18 * h), date_y - int(0.12 * h))
                    return {
                        'slug': slug,
                        'date': date_str,
                        'time': time_str,
                        'click_x': int(w * 0.50),
                        'click_y': outcome_y,
                        'date_y': date_y
                    }
        return None

    def find_player_battle_rows(self, pil_image: Image.Image):
        """
        Find all clickable battle rows on the current screen by locating friendly player entries.
        Works across all window resolutions (720p, 1080p, 1501x844, 2K, 4K).
        Returns list of dicts sorted strictly from TOP to BOTTOM:
        [
            {'y': 306, 'click_x': 500, 'click_y': 316, 'text': '哆1绮罗生'},
            ...
        ]
        """
        if not winocr or pil_image is None:
            return []

        w, h = pil_image.size
        try:
            async def _runner():
                return await winocr.recognize_pil(pil_image, lang=self.lang)
            res = asyncio.run(_runner())
        except Exception:
            return []

        rows = []
        for l in res.lines:
            t = l.text.replace(' ', '')
            if not l.words:
                continue
            w0 = l.words[0]
            # Friendly player name column is in x: 0.15*w to 0.45*w and y: 0.18*h to 0.90*h
            if int(0.15 * w) < w0.bounding_rect.x < int(0.45 * w) and int(0.18 * h) < w0.bounding_rect.y < int(0.90 * h):
                if any(k in t for k in ['绮罗生', '罗生', '哆', '我', '守', '部']):
                    xs = [word.bounding_rect.x for word in l.words]
                    ys = [word.bounding_rect.y for word in l.words]
                    ws = [word.bounding_rect.width for word in l.words]
                    hs = [word.bounding_rect.height for word in l.words]
                    min_x = int(min(xs))
                    min_y = int(min(ys))
                    max_x = int(max([x+w for x, w in zip(xs, ws)]))
                    max_y = int(max([y+h for y, h in zip(ys, hs)]))
                    rows.append({
                        'y': min_y,
                        'click_x': (min_x + max_x) // 2,
                        'click_y': (min_y + max_y) // 2,
                        'text': t
                    })

        # Deduplicate rows within 30 pixels of each other
        unique_rows = []
        rows.sort(key=lambda r: r['y'])
        for r in rows:
            if not any(abs(r['y'] - u['y']) < int(0.04 * h) for u in unique_rows):
                unique_rows.append(r)

        return unique_rows

    def find_return_button(self, pil_image: Image.Image):
        """Find the exact center (x, y) of the top-right Return button via OCR."""
        if not winocr or pil_image is None:
            return None
        w, h = pil_image.size
        try:
            async def _runner():
                return await winocr.recognize_pil(pil_image, lang=self.lang)
            res = asyncio.run(_runner())
        except Exception:
            return None

        for l in res.lines:
            t = l.text.replace(' ', '')
            if not l.words:
                continue
            w0 = l.words[0]
            # Must be in top 15% and right half of the screen
            if w0.bounding_rect.y < int(0.15 * h) and w0.bounding_rect.x > int(0.60 * w):
                if any(k in t for k in ['返', '回']):
                    xs = [word.bounding_rect.x for word in l.words]
                    ys = [word.bounding_rect.y for word in l.words]
                    ws = [word.bounding_rect.width for word in l.words]
                    hs = [word.bounding_rect.height for word in l.words]
                    return (int(min(xs) + sum(ws)/2), int(min(ys) + sum(hs)/2))
        return None
