"""
ZenLink 投屏窗口操作内核
- 截图：PrintWindow(PW_RENDERFULLCONTENT=2) 抓 ZenLinkVideo 硬件渲染画面
- 点击：PostMessage 后台注入（首选）/ 物理鼠标点击（备选）

纯 ctypes 实现，不强制依赖 pywin32。
"""
import ctypes
import ctypes.wintypes as wt
import time
import logging

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
MK_LBUTTON = 0x0001
SW_RESTORE = 9

log = logging.getLogger("OnmyojiPilot")


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG),
        ("top", wt.LONG),
        ("right", wt.LONG),
        ("bottom", wt.LONG),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

# 基准分辨率：所有模板/坐标都按此标定
BASE_W = 878
BASE_H = 404


def fit_transform(width, height, base_w=BASE_W, base_h=BASE_H):
    """
    把"基准坐标"映射到"当前窗口坐标"的等比变换（contain 方式）。

    ZenLink 的视频保持手机原始宽高比（约 2.17:1），但窗口可以被拉成任意尺寸，
    多出来的空间会变成黑边。所以**不能**分别用 width/height 各算一个比例
    （非等比会把画面拉伸，模板直接失配 —— 实测 878x458 时 fire_btn 从 0.95 掉到 0.11）。

    正确做法：取两个方向里较小的比例做统一缩放，再把画面居中，
    余下部分就是黑边偏移。

    Returns:
        (scale, offset_x, offset_y)
    """
    scale = min(width / base_w, height / base_h)
    offset_x = (width - base_w * scale) / 2.0
    offset_y = (height - base_h * scale) / 2.0
    return scale, offset_x, offset_y


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


class WindowDevice:
    """封装 ZenLink 投屏窗口的截图与点击"""

    # 基准分辨率：所有模板/坐标按此标定，运行时自动按比例换算
    BASE_W = 878
    BASE_H = 404

    def __init__(self, main_title="ZenLink", main_class="Window Class",
                 video_class="ZenLinkVideo", click_mode="message"):
        self.main_title = main_title
        self.main_class = main_class
        self.video_class = video_class
        self.click_mode = click_mode  # "message" 或 "physical"
        self.main_hwnd = None
        self.video_hwnd = None
        self.width = 0
        self.height = 0
        # 本次截图是否为"新鲜帧"。PrintWindow 在战斗加载/界面切换瞬间会失败，
        # 此时 screenshot() 返回上一帧缓存 —— 缓存帧不能用于界面判断，
        # 否则会出现"挑战按钮明明消失了却还在等"的误判。
        self.frame_fresh = False

    def scale_click(self, x, y):
        """把基准坐标(878x404)换算到当前窗口坐标（含黑边偏移）"""
        scale, ox, oy = fit_transform(self.width, self.height, self.BASE_W, self.BASE_H)
        return round(ox + x * scale), round(oy + y * scale)

    # ---------- 窗口定位 ----------
    def find_window(self) -> bool:
        """每轮任务开始前调用，重新定位窗口（ZenLink 可能重启导致句柄变化）"""
        found_main = []

        def cb(hwnd, _):
            if _window_text(hwnd) == self.main_title and _class_name(hwnd) == self.main_class:
                found_main.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        if not found_main:
            log.error("未找到 ZenLink 主窗口（标题=%s 类名=%s）", self.main_title, self.main_class)
            return False

        self.main_hwnd = found_main[0]

        found_video = []

        def cb2(hwnd, _):
            if _class_name(hwnd) == self.video_class:
                found_video.append(hwnd)
            return True

        user32.EnumChildWindows(self.main_hwnd, WNDENUMPROC(cb2), 0)
        if not found_video:
            log.error("未找到投屏子窗口（类名=%s）", self.video_class)
            return False

        self.video_hwnd = found_video[0]

        # 若最小化则还原；被屏幕外则强行还原
        rect = RECT()
        user32.GetWindowRect(self.video_hwnd, ctypes.byref(rect))
        if user32.IsIconic(self.main_hwnd) or rect.left < -10000:
            log.info("检测到 ZenLink 最小化/隐藏，正在还原窗口")
            user32.ShowWindow(self.main_hwnd, SW_RESTORE)
            # 还原后投屏画面需要一点时间重建，太早点点击会被丢弃
            # （实测：还原后立刻 PostMessage 点击挑战无效，按钮 15s 都不消失）
            time.sleep(1.2)
            user32.GetWindowRect(self.video_hwnd, ctypes.byref(rect))

        self.width = rect.right - rect.left
        self.height = rect.bottom - rect.top
        log.info("投屏窗口已定位: hwnd=%s 尺寸=%dx%d", self.video_hwnd, self.width, self.height)
        return True

    @staticmethod
    def is_idle_frame(img):
        """
        判断这帧是不是 ZenLink 自己的"未投屏"界面（而不是游戏画面）。

        依据（实测数据）：
                        主色占比   灰度std
          未投屏界面     88.5%     19.9
          掉落结算页     18.6%     42.2
          御魂副本页     15.0%     76.8
          战斗中         15.0%     76.7

        ZenLink 未投屏时是深色点阵背景 + 绿色手机图标，画面高度单一；
        游戏画面色彩丰富。两个条件同时满足才判"未投屏"，避免误伤暗色场景。

        Args:
            img: RGB 或 BGR 的 (h,w,3) 数组

        Returns:
            bool
        """
        import numpy as np

        if img is None or img.ndim != 3 or img.size == 0:
            return False
        # 5-bit 量化后统计主色占比（bincount 比 np.unique 快一个量级）
        q = (((img[:, :, 0].astype(np.uint32) >> 3) << 10)
             | ((img[:, :, 1].astype(np.uint32) >> 3) << 5)
             | (img[:, :, 2].astype(np.uint32) >> 3))
        counts = np.bincount(q.ravel(), minlength=32768)
        dominant = counts.max() / q.size
        gray_std = float(img.mean(axis=2).std())
        return dominant > 0.55 and gray_std < 35.0

    # ---------- 截图 ----------
    def _restore_if_minimized(self):
        """
        窗口被最小化时 PrintWindow 会**永久失败**（实测连续 60+ 帧抓不到图，
        任务卡死在等待循环里）。运行期必须能自愈，不能只在启动时还原一次。
        """
        if not self.main_hwnd:
            return False
        if not user32.IsIconic(self.main_hwnd):
            return False
        log.info("投屏窗口被最小化，正在还原")
        user32.ShowWindow(self.main_hwnd, SW_RESTORE)
        time.sleep(0.9)
        rect = RECT()
        user32.GetWindowRect(self.video_hwnd, ctypes.byref(rect))
        self.width = rect.right - rect.left
        self.height = rect.bottom - rect.top
        log.info("窗口已还原，尺寸 %dx%d", self.width, self.height)
        return True

    def screenshot(self, retries=5):
        """
        返回 numpy RGB 数组 (height, width, 3)
        PrintWindow 对硬件渲染的投屏窗口是唯一可行的后台截图方式。
        战斗加载/界面切换瞬间 PrintWindow 会短暂失败，静默重试即可。
        窗口最小化导致的长时失败由 _restore_if_minimized 自愈。
        """
        import numpy as np

        if not self.video_hwnd:
            raise RuntimeError("窗口未定位，请先调用 find_window()")

        last_ok = False

        for attempt in range(retries):
            w, h = self.width, self.height
            if w <= 0 or h <= 0:
                self._restore_if_minimized()
                w, h = self.width, self.height
                if w <= 0 or h <= 0:
                    time.sleep(0.2)
                    continue

            screen_dc = user32.GetDC(0)
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
            old = gdi32.SelectObject(mem_dc, bitmap)

            ok = user32.PrintWindow(self.video_hwnd, mem_dc, 2)
            last_ok = bool(ok)

            raw = ctypes.create_string_buffer(w * h * 4)
            gdi32.GetBitmapBits(bitmap, w * h * 4, raw)

            # 先把旧对象选回去再删 bitmap，否则 bitmap 仍被 DC 持有，
            # DeleteObject 不会真正释放（GDI 句柄泄漏）。
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(0, screen_dc)

            if ok:
                img = np.frombuffer(raw.raw, dtype=np.uint8).reshape(h, w, 4)
                result = img[:, :, :3][:, :, ::-1].copy()  # BGR->RGB
                self._last_image = result
                self.frame_fresh = True
                self.stale_count = 0
                return result

            # 失败：可能被最小化了，尝试自愈后再试
            if self._restore_if_minimized():
                continue
            time.sleep(0.2)

        # 全部失败：返回上一帧缓存或全黑，避免任务崩溃
        self.frame_fresh = False
        self.stale_count = getattr(self, "stale_count", 0) + 1
        if getattr(self, "_last_image", None) is not None:
            log.debug("截图失败，复用上一帧")
            return self._last_image
        log.warning("PrintWindow 连续 %d 次失败，返回黑帧", retries)
        return np.zeros((max(h, 1), max(w, 1), 3), dtype=np.uint8)

    # ---------- 点击 ----------
    def click(self, x: int, y: int, duration: float = 0.08):
        """点击投屏窗口内坐标（基准878x404坐标，自动换算到当前尺寸）"""
        x, y = self.scale_click(x, y)
        if self.click_mode == "physical":
            self._physical_click(x, y, duration)
        else:
            self._message_click(x, y, duration)

    def _message_click(self, x, y, duration):
        """后台消息点击：窗口可被遮挡，不影响其他工作"""
        lparam = (int(y) << 16) | (int(x) & 0xFFFF)
        user32.PostMessageW(self.video_hwnd, WM_MOUSEMOVE, 0, lparam)
        user32.PostMessageW(self.video_hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(duration)
        user32.PostMessageW(self.video_hwnd, WM_LBUTTONUP, 0, lparam)

    def _physical_click(self, x, y, duration):
        """物理鼠标点击：一定生效，但会占用鼠标、要求窗口在前台"""
        rect = RECT()
        user32.GetWindowRect(self.video_hwnd, ctypes.byref(rect))
        screen_x = rect.left + int(x)
        screen_y = rect.top + int(y)
        user32.SetCursorPos(screen_x, screen_y)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(duration)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP

    def swipe(self, x1, y1, x2, y2, duration=0.3):
        """滑动（用于翻页/拖动）"""
        steps = 10
        for i in range(steps + 1):
            x = int(x1 + (x2 - x1) * i / steps)
            y = int(y1 + (y2 - y1) * i / steps)
            lparam = (y << 16) | (x & 0xFFFF)
            if i == 0:
                user32.PostMessageW(self.video_hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
            else:
                user32.PostMessageW(self.video_hwnd, WM_MOUSEMOVE, MK_LBUTTON, lparam)
            time.sleep(duration / steps)
        user32.PostMessageW(self.video_hwnd, WM_LBUTTONUP, 0, lparam)
