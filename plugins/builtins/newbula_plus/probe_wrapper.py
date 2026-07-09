"""探头包装器 — 精简版，只保留 CDP + JS 读取"""
import asyncio
import ctypes
import json
import struct
import websockets
import requests
import time
import traceback
import uuid
import re
from io import BytesIO
from pathlib import Path
from PIL import Image
from ctypes import wintypes
import pyautogui, pyperclip

import core
from plugins.builtins.screen_probe.probe import ProbeInstance, DevourViewfinder


class NewbulaProbe(ProbeInstance):
    """Newbula Plus 探头 — 精简版"""

    def __init__(self, agent, probe_id, name, region, hwnd_target,
                 chrome_port, platform_id, reader_config=None, **kwargs):
        print(f"[NewbulaProbe.__init__] {name}")
        self._target_pos = kwargs.pop("position", None)

        super().__init__(
            agent=agent,
            probe_id=probe_id,
            name=name,
            region=region,
            interval=kwargs.get("interval", 1),
            rules=kwargs.get("rules", []),
            mode="cdp",
            hwnd_target=hwnd_target
        )
        self.chrome_port = chrome_port
        self.platform_id = platform_id
        self._cdp_ws = None
        self._cdp_connected = False
        self._current_request_id = None
        
        self.reader_config = reader_config or {
            "type": "fallback",
            "selector": '[class*="message"]',
            "id_marker": "（ID={id}）"
        }
        
        print(f"[NewbulaProbe.__init__] reader_type: {self.reader_config.get('type', 'unknown')}")

    # ============================================================
    # 吞噬窗口
    # ============================================================
    def _create_viewfinder(self):
        try:
            abs_x1 = self.region["x1"]
            abs_y1 = self.region["y1"]
            abs_w = self.region["x2"] - self.region["x1"]
            abs_h = self.region["y2"] - self.region["y1"]

            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self.hwnd_target, ctypes.byref(rect))
            win_x1, win_y1 = rect.left, rect.top

            rel_x = abs_x1 - win_x1
            rel_y = abs_y1 - win_y1
            self._crop_rel_region = (rel_x, rel_y, abs_w, abs_h)

            pos = self._target_pos if self._target_pos else (abs_x1, abs_y1)

            self._viewfinder = DevourViewfinder(
                self.id, self.name, self.hwnd_target,
                (abs_x1, abs_y1, abs_w, abs_h),
                self._crop_rel_region,
                on_close_callback=self._on_viewfinder_closed
            )

            self._viewfinder.SetPosition(pos)
            self._viewfinder.SetSize((abs_w, abs_h + 32))

            self.agent.loop.call_soon_threadsafe(self._viewfinder_ready.set)
            print(f"[NewbulaProbe] viewfinder 创建: pos={pos}, size=({abs_w}, {abs_h + 32})")
        except Exception as e:
            print(f"[NewbulaProbe._create_viewfinder] error: {e}")
            traceback.print_exc()

    # ============================================================
    # CDP 通信
    # ============================================================
    async def _cdp_send_and_wait(self, expression, timeout=5):
        request_id = int(time.time() * 1000) % 100000
        
        await self._cdp_ws.send(json.dumps({
            "id": request_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True}
        }))
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                raw = await asyncio.wait_for(self._cdp_ws.recv(), timeout=1)
                data = json.loads(raw)
                if "id" in data and data["id"] == request_id:
                    if "error" in data:
                        return None
                    return data.get("result", {}).get("result", {}).get("value", "")
            except asyncio.TimeoutError:
                continue
        return None

    async def connect_cdp(self):
        print(f"[NewbulaProbe.connect_cdp] {self.name}")
        try:
            resp = requests.get(f"http://localhost:{self.chrome_port}/json", timeout=3)
            tabs = resp.json()
            target_tab = None
            for tab in tabs:
                if tab.get("type") == "page":
                    target_tab = tab
                    break
            if not target_tab:
                print("[NewbulaProbe.connect_cdp] 未找到页面标签页")
                return False
            
            ws_url = target_tab.get("webSocketDebuggerUrl")
            if not ws_url:
                return False
            
            self._cdp_ws = await websockets.connect(ws_url)
            for _ in range(30):
                ready = await self._cdp_send_and_wait("document.readyState || ''")
                if ready == "complete":
                    break
                await asyncio.sleep(1)
            
            await asyncio.sleep(3)
            self._cdp_connected = True
            print(f"[NewbulaProbe] CDP 连接成功: {self.name}")
            return True
        except Exception as e:
            print(f"[NewbulaProbe] CDP 连接失败: {e}")
            return False

    # ============================================================
    # 剪贴板操作
    # ============================================================
    def _copy_file_to_clipboard(self, file_path):
        try:
            import win32clipboard
            import os
            abs_path = os.path.abspath(file_path)
            dropfiles = (struct.pack('I', 20) + struct.pack('I', 0) + struct.pack('I', 0) +
                         struct.pack('I', 0) + struct.pack('I', 1) +
                         (abs_path + '\x00').encode('utf-16le') + b'\x00\x00')
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_HDROP, dropfiles)
            win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            print(f"[NewbulaProbe] File copy failed: {e}")
            return False

    def _copy_image_to_clipboard(self, image_path: str):
        try:
            import win32clipboard
            img = Image.open(image_path)
            output = BytesIO()
            img.convert("RGB").save(output, format="BMP")
            data = output.getvalue()
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data[14:])
            win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            print(f"[NewbulaProbe] Image copy failed: {e}")
            return False

    # ============================================================
    # 输入操作
    # ============================================================
    def _get_input_center(self):
        container = self._viewfinder.container
        hwnd = container.GetHandle()
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2

    async def _focus_and_click(self):
        container = self._viewfinder.container
        hwnd = container.GetHandle()
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        await asyncio.sleep(0.2)
        x, y = self._get_input_center()
        pyautogui.click(x, y)
        await asyncio.sleep(0.3)

    async def _relocate_input(self):
        js = """
        (function() {
            const inputs = document.querySelectorAll('textarea, [contenteditable="true"], input[type="text"]');
            for (let inp of inputs) {
                if (inp.offsetParent !== null) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.width > 50 && rect.height > 20) {
                        return JSON.stringify({x: rect.left + rect.width/2, y: rect.top + rect.height/2, w: rect.width, h: rect.height});
                    }
                }
            }
            return 'null';
        })();
        """
        result = await self._cdp_send_and_wait(js, timeout=3)
        if result and result != 'null':
            pos = json.loads(result)
            print(f"[NewbulaProbe._relocate_input] {self.name} x={pos['x']:.0f}, y={pos['y']:.0f}")
            return pos
        return None

    # ============================================================
    # 发送
    # ============================================================
    async def send(self, text: str = "", images: list = None, files: list = None):
        if not self._viewfinder:
            return {"error": "viewfinder not ready"}

        self._current_request_id = str(uuid.uuid4())[:8]
        rid = self._current_request_id

        if text.strip():
            wrapped = f"不要回复ID（ID={rid}）\n{text}"
        else:
            wrapped = f"（ID={rid}）"

        print(f"[NewbulaProbe.send] {self.name} ID: {rid}, 文字:{len(text)}, 图片:{len(images or [])}, 文件:{len(files or [])}")

        await self._relocate_input()
        await self._focus_and_click()

        pyperclip.copy(wrapped)
        pyautogui.hotkey('ctrl', 'v')
        await asyncio.sleep(0.2)

        for img in (images or []):
            self._copy_image_to_clipboard(img)
            pyautogui.hotkey('ctrl', 'v')
            await asyncio.sleep(1)

        for f in (files or []):
            self._copy_file_to_clipboard(f)
            pyautogui.hotkey('ctrl', 'v')
            await asyncio.sleep(1)

        pyautogui.press('enter')
        print(f"[NewbulaProbe.send] ✅")
        return {"ok": True, "request_id": rid}

    # ============================================================
    # 读取回复（核心：JS 文件）
    # ============================================================
    async def get_reply(self):
        if not self._cdp_connected or not self._current_request_id:
            return ""

        reader_config = self.reader_config or {}
        rid = self._current_request_id
        
        js_file = reader_config.get("file", "")
        if not js_file:
            return ""
        
        full_path = Path(__file__).parent / js_file.lstrip("./")
        if not full_path.exists():
            print(f"[get_reply] JS文件不存在: {full_path}")
            return ""
        
        js = full_path.read_text(encoding="utf-8").replace("{rid}", rid)
        reply = await self._cdp_send_and_wait(js)
        
        if reply and len(reply) > 10:
            return reply[:50000]
        return ""

    # ============================================================
    # 等待回复
    # ============================================================
    async def wait_for_reply(self, timeout=60, check_interval=0.5, is_generate=False):
        """
        等待回复完成（检测到内容就返回）
        - 文字模式：只要有内容就返回
        - 生图模式：也返回 HTML，前端自己渲染图片
        """
        print(f"[wait_for_reply] {self.name} 开始，超时:{timeout}s")
        start = time.time()

        while time.time() - start < timeout:
            reply = await self.get_reply()
            if reply and len(reply) > 10:
                print(f"[wait_for_reply] ✅ {self.name} 获取到内容，长度: {len(reply)}")
                return {"reply": reply, "media_urls": []}
            await asyncio.sleep(check_interval)

        print(f"[wait_for_reply] ⏰ {self.name} 超时")
        return {"reply": "", "media_urls": []}

    # ============================================================
    # 组合发送（对外接口）
    # ============================================================
    async def send_and_wait(self, text: str, timeout=60):
        await self.send(text=text)
        return await self.wait_for_reply(timeout)

    async def send_image_and_wait(self, image_path: str, text: str = None, timeout=120):
        await self.send(text=text or "", images=[image_path])
        return await self.wait_for_reply(timeout)

    async def send_file_and_wait(self, file_path: str, text: str = None, timeout=120):
        await self.send(text=text or "", files=[file_path])
        return await self.wait_for_reply(timeout)

    async def generate_media(self, query: str, image_path: str = None, file_path: str = None, timeout=120):
        images = [image_path] if image_path and Path(image_path).exists() else []
        files = [file_path] if file_path and Path(file_path).exists() else []
        await self.send(text=query, images=images, files=files)
        return await self.wait_for_reply(timeout, is_generate=True)