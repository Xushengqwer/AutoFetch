import os
import sys
import asyncio
import winocr
import cv2
import numpy as np
from PIL import Image

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.game_window import GameWindow

def inspect_current_screen(out_path="live_analysis.png"):
    gw = GameWindow()
    hwnd = gw.find_window()
    if not hwnd:
        print("[ERROR] Game window not found! Please open S3Client.exe.")
        return None

    gw.activate_window()
    box = gw.get_client_box()
    screen_pil = gw.capture_client()
    if screen_pil is None:
        print("[ERROR] Failed to capture client screen.")
        return None

    w, h = screen_pil.size
    print(f"[INFO] Captured game screen: {w}x{h}")

    # Run OCR to get all bounding boxes
    async def get_ocr():
        return await winocr.recognize_pil(screen_pil, lang="zh-Hans")
    
    res = asyncio.run(get_ocr())
    img_cv = cv2.cvtColor(np.array(screen_pil), cv2.COLOR_RGB2BGR)

    elements = []
    for line in res.lines:
        text = line.text.replace(" ", "")
        if not line.words:
            continue
        xs = [int(word.bounding_rect.x) for word in line.words]
        ys = [int(word.bounding_rect.y) for word in line.words]
        ws = [int(word.bounding_rect.width) for word in line.words]
        hs = [int(word.bounding_rect.height) for word in line.words]
        min_x = min(xs)
        min_y = min(ys)
        max_x = max([x+w for x, w in zip(xs, ws)])
        max_y = max([y+h for y, h in zip(ys, hs)])

        elements.append({
            "text": text,
            "box": (min_x, min_y, max_x, max_y),
            "center": ((min_x + max_x) // 2, (min_y + max_y) // 2)
        })

        # Draw green bounding box for battle timestamps / key buttons
        if any(k in text for k in ["2026", "26年", "返回", "个人战报", "交战", "鏖战"]):
            cv2.rectangle(img_cv, (min_x, min_y), (max_x, max_y), (0, 255, 0), 2)
            cv2.putText(img_cv, text, (min_x, max(15, min_y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    cv2.imwrite(out_path, img_cv)
    print(f"[SUCCESS] Saved annotated screen analysis to {out_path}")
    print(f"[INFO] Total text elements recognized: {len(elements)}")
    return elements

if __name__ == "__main__":
    inspect_current_screen()
