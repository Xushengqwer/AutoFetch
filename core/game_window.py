import ctypes
from ctypes import wintypes
import time

# Set Per-Monitor DPI Aware
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import win32gui
import win32con
import win32process
import win32service
import mss
from PIL import Image

def attach_to_default_desktop():
    """Ensure current thread can access the interactive default desktop."""
    try:
        desk = win32service.OpenDesktop('default', 0, False, win32con.GENERIC_ALL)
        desk.SetThreadDesktop()
    except Exception:
        pass

class GameWindow:
    def __init__(self, target_classes=("EJOY2DX",), target_processes=("s3client",)):
        self.target_classes = [c.lower() for c in target_classes]
        self.target_processes = [p.lower() for p in target_processes]
        self.hwnd = None

    def find_window(self):
        attach_to_default_desktop()
        self.hwnd = None

        candidates = []
        def enum_cb(h, _):
            if win32gui.IsWindowVisible(h):
                cls = win32gui.GetClassName(h).lower()
                txt = win32gui.GetWindowText(h)
                _, pid = win32process.GetWindowThreadProcessId(h)

                # Check class first (most reliable for EJOY2DX)
                if any(tc in cls for tc in self.target_classes):
                    candidates.insert(0, h)
                    return True

                # Check title with S3Client / 模拟器
                if "1454" in txt or ("三国志" in txt and "模拟器" in txt):
                    candidates.append(h)
            return True

        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            pass

        if candidates:
            self.hwnd = candidates[0]
        return self.hwnd

    def activate(self):
        """Bring the game window to foreground and ensure it is restored from minimized state."""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            if not self.find_window():
                return False

        attach_to_default_desktop()

        try:
            # Force restore if minimized
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            pass

        # Wait up to 1.5s for window to have valid dimensions
        for _ in range(15):
            crect = win32gui.GetClientRect(self.hwnd)
            if (crect[2] - crect[0]) > 200 and (crect[3] - crect[1]) > 200:
                break
            time.sleep(0.1)

        time.sleep(0.3)
        return True

    def normalize_window(self, target_client_w=1280, target_client_h=720):
        """
        Normalize the game client area to standard 16:9 (1280x720).
        This eliminates screen resolution, display scaling, and multi-monitor DPI differences!
        """
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            if not self.find_window():
                return False

        try:
            # 1. Restore if maximized or minimized
            placement = win32gui.GetWindowPlacement(self.hwnd)
            if placement[1] in (win32con.SW_SHOWMAXIMIZED, win32con.SW_SHOWMINIMIZED):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                time.sleep(0.3)

            # 2. Compute non-client border / titlebar thickness
            wrect = win32gui.GetWindowRect(self.hwnd)
            crect = win32gui.GetClientRect(self.hwnd)
            curr_w = crect[2] - crect[0]
            curr_h = crect[3] - crect[1]

            # If already normalized within 5 pixels, skip
            if abs(curr_w - target_client_w) <= 5 and abs(curr_h - target_client_h) <= 5:
                return True

            border_w = (wrect[2] - wrect[0]) - curr_w
            border_h = (wrect[3] - wrect[1]) - curr_h

            total_w = target_client_w + max(0, border_w)
            total_h = target_client_h + max(0, border_h)

            # Keep top-left visible on screen
            cur_x = max(20, min(wrect[0], 200))
            cur_y = max(20, min(wrect[1], 100))

            win32gui.SetWindowPos(
                self.hwnd,
                win32con.HWND_TOP,
                cur_x, cur_y,
                total_w, total_h,
                win32con.SWP_SHOWWINDOW
            )
            time.sleep(0.4)
            return True
        except Exception:
            return False

    def get_client_box(self):
        """Return (left, top, width, height) of the client area in physical screen coordinates."""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            if not self.find_window():
                return None

        # Check size and retry restore if needed
        for _ in range(10):
            crect = win32gui.GetClientRect(self.hwnd)
            width = crect[2] - crect[0]
            height = crect[3] - crect[1]
            if width > 100 and height > 100:
                origin = win32gui.ClientToScreen(self.hwnd, (0, 0))
                return {
                    'left': origin[0],
                    'top': origin[1],
                    'width': width,
                    'height': height
                }
            try:
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
            time.sleep(0.15)

        return None

    def capture_client(self):
        """Capture the current game client area as a PIL Image."""
        attach_to_default_desktop()
        box = self.get_client_box()
        if not box or box['width'] <= 0 or box['height'] <= 0:
            return None

        with mss.MSS() as sct:
            sct_img = sct.grab(box)
            return Image.frombytes('RGB', sct_img.size, sct_img.bgra, 'raw', 'BGRX')
