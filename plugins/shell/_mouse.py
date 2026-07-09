"""鼠标钩子插件 — 协议 v3.0 系统插件
监听鼠标点击事件，将坐标和按钮信息发送给目标插件。
"""
import asyncio
import threading
from typing import Optional

_listener: Optional["MouseListener"] = None


class MouseListener:
    def __init__(self, agent):
        self.agent = agent
        self._click_hooks = {}  # button → receiver
        self._move_hook = None
        self._global_hook = None
        self._running = False
        self._thread = None
        self._last_x = 0
        self._last_y = 0
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
    
    def _listen(self):
        from pynput import mouse
        
        def on_click(x, y, button, pressed):
            self._last_x = x
            self._last_y = y
            button_str = str(button).replace("Button.", "")
            loop = self.agent.loop
            
            if pressed:
                if button_str in self._click_hooks:
                    receiver = self._click_hooks[button_str]
                    env = core.Envelop(
                        sender="shell/_mouse",
                        receiver=receiver,
                        payload={"x": x, "y": y, "button": button_str, "event": "click"},
                    )
                    asyncio.run_coroutine_threadsafe(
                        self.agent.system.call(env), loop
                    )
                
                if self._global_hook:
                    env = core.Envelop(
                        sender="shell/_mouse",
                        receiver=self._global_hook,
                        payload={"x": x, "y": y, "button": button_str, "event": "click"},
                    )
                    asyncio.run_coroutine_threadsafe(
                        self.agent.system.call(env), loop
                    )
        
        def on_move(x, y):
            self._last_x = x
            self._last_y = y
            
            if self._move_hook:
                loop = self.agent.loop
                env = core.Envelop(
                    sender="shell/_mouse",
                    receiver=self._move_hook,
                    payload={"x": x, "y": y, "event": "move"},
                )
                asyncio.run_coroutine_threadsafe(
                    self.agent.system.call(env), loop
                )
        
        with mouse.Listener(on_click=on_click, on_move=on_move) as listener:
            self._listener_ref = listener
            listener.join()
    
    def stop(self):
        self._running = False
        if hasattr(self, '_listener_ref'):
            self._listener_ref.stop()


import core


async def execute(envelop, agent):
    global _listener
    
    if _listener is None:
        _listener = MouseListener(agent)
    
    action = envelop.payload.get("action", "")
    
    if action == "start":
        _listener.start()
        envelop.payload = {"ok": True, "status": "listening"}
        return envelop
    
    elif action == "stop":
        _listener.stop()
        envelop.payload = {"ok": True, "status": "stopped"}
        return envelop
    
    elif action == "hook_click":
        button = envelop.payload.get("button", "left")
        receiver = envelop.payload.get("receiver", "")
        
        if not receiver:
            envelop.payload = {"error": "receiver required"}
            return envelop
        
        _listener._click_hooks[button] = receiver
        envelop.payload = {"ok": True, "button": button, "receiver": receiver}
        return envelop
    
    elif action == "hook_move":
        receiver = envelop.payload.get("receiver", "")
        _listener._move_hook = receiver or None
        envelop.payload = {"ok": True, "receiver": receiver}
        return envelop
    
    elif action == "global_hook":
        receiver = envelop.payload.get("receiver", "")
        _listener._global_hook = receiver or None
        envelop.payload = {"ok": True, "receiver": receiver}
        return envelop
    
    elif action == "position":
        envelop.payload = {"x": _listener._last_x, "y": _listener._last_y}
        return envelop
    
    elif action == "unhook":
        button = envelop.payload.get("button", "")
        _listener._click_hooks.pop(button, None)
        envelop.payload = {"ok": True}
        return envelop
    
    elif action == "list_hooks":
        envelop.payload = {
            "click_hooks": dict(_listener._click_hooks),
            "move_hook": _listener._move_hook,
            "global_hook": _listener._global_hook,
            "running": _listener._running,
            "position": {"x": _listener._last_x, "y": _listener._last_y},
        }
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop