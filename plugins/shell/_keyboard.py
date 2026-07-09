"""键盘监听插件 — 协议 v3.0 系统插件
监听键盘事件，将按键转为 Envelop 发送给目标插件。
"""
import asyncio
import threading
from typing import Optional

_listener: Optional["KeyboardListener"] = None


class KeyboardListener:
    def __init__(self, agent):
        self.agent = agent
        self._hooks = {}  # key → receiver
        self._global_hook = None  # 全局回调的 receiver
        self._running = False
        self._thread = None
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
    
    def _listen(self):
        from pynput import keyboard
        
        def on_press(key):
            try:
                key_str = str(key).replace("'", "")
            except Exception:
                key_str = str(key)
            
            loop = self.agent.loop
            
            # 特定按键回调
            if key_str in self._hooks:
                receiver = self._hooks[key_str]
                env = core.Envelop(
                    sender="shell/_keyboard",
                    receiver=receiver,
                    payload={"key": key_str, "event": "press"},
                )
                asyncio.run_coroutine_threadsafe(
                    self.agent.system.call(env), loop
                )
            
            # 全局回调
            if self._global_hook:
                env = core.Envelop(
                    sender="shell/_keyboard",
                    receiver=self._global_hook,
                    payload={"key": key_str, "event": "press"},
                )
                asyncio.run_coroutine_threadsafe(
                    self.agent.system.call(env), loop
                )
        
        with keyboard.Listener(on_press=on_press) as listener:
            self._listener_ref = listener
            listener.join()
    
    def stop(self):
        self._running = False
        if hasattr(self, '_listener_ref'):
            self._listener_ref.stop()


import core


async def execute(envelop, agent):
    global _listener
    
    action = envelop.payload.get("action", "")
    
    if _listener is None:
        _listener = KeyboardListener(agent)
    
    if action == "start":
        _listener.start()
        envelop.payload = {"ok": True, "status": "listening"}
        return envelop
    
    elif action == "stop":
        _listener.stop()
        envelop.payload = {"ok": True, "status": "stopped"}
        return envelop
    
    elif action == "hook":
        key = envelop.payload.get("key", "")
        receiver = envelop.payload.get("receiver", "")
        
        if not key or not receiver:
            envelop.payload = {"error": "key and receiver required"}
            return envelop
        
        _listener._hooks[key] = receiver
        envelop.payload = {"ok": True, "key": key, "receiver": receiver}
        return envelop
    
    elif action == "unhook":
        key = envelop.payload.get("key", "")
        _listener._hooks.pop(key, None)
        envelop.payload = {"ok": True, "key": key}
        return envelop
    
    elif action == "global_hook":
        receiver = envelop.payload.get("receiver", "")
        _listener._global_hook = receiver
        envelop.payload = {"ok": True, "receiver": receiver}
        return envelop
    
    elif action == "list_hooks":
        envelop.payload = {
            "hooks": dict(_listener._hooks),
            "global_hook": _listener._global_hook,
            "running": _listener._running,
        }
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop