"""System 能力聚合器 — 协议 v3.0"""
import asyncio
import threading


class System:
    """能力聚合器 — 挂载到 agent.system"""

    def __init__(self, loop: asyncio.AbstractEventLoop, route_fn, on_shutdown=None):
        self.loop = loop
        self._route = route_fn
        self._agent = None

        from plugins.builtins.system.gui import SystemGUI
        self._gui = SystemGUI()

        # 动态绑定 gui 的所有公开方法到 self
        for name in dir(self._gui):
            if name.startswith('_'):
                continue
            attr = getattr(self._gui, name)
            if callable(attr):
                setattr(self, name, attr)

        self._mouse_listener = None
        self._mouse_callback = None
        self.on_shutdown = on_shutdown

    def bind(self, agent):
        self._agent = agent

    async def call(self, envelop):
        return await self._route(envelop, self._agent)

    def start_gui(self):
        self._gui.start()

    # ── 鼠标钩子 ──

    def start_mouse_hook(self, callback):
        if self._mouse_listener is not None:
            return
        from pynput import mouse
        self._mouse_callback = callback
        self._mouse_listener = mouse.Listener(on_click=self._on_mouse_event)
        self._mouse_listener.start()

    def _on_mouse_event(self, x, y, button, pressed):
        if self._mouse_callback:
            try:
                self._mouse_callback(x, y, button, pressed)
            except Exception as e:
                print(f"[System] 鼠标回调异常: {e}")
                import traceback
                traceback.print_exc()

    def stop_mouse_hook(self):
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None