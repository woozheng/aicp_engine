
"""系统托盘管理插件 — 协议 v3.0 系统插件"""
import asyncio
import threading
import queue
import traceback
import webbrowser
from pathlib import Path

_tray_instance = None


class TrayManager:
    
    def __init__(self, loop, on_shutdown=None):
        self.loop = loop
        self._on_shutdown = on_shutdown
        self._tray = None
        self._menu_items = {}
        self._app_callbacks = {}
        self._ready = threading.Event()
    
    def start(self):
        threading.Thread(target=self._run, daemon=True, name="tray-thread").start()
        self._ready.wait(timeout=10)
    
    def _run(self):
        import tkinter as tk
        try:
            self._root = tk.Tk()
            self._root.withdraw()
            self._setup_tray()
            self._ready.set()
            if self._tray:
                self._tray.run()
            self._root.mainloop()
        except Exception as e:
            traceback.print_exc()
            self._ready.set()
    
    def _setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            
            img = Image.new('RGB', (16, 16), '#6366f1')
            draw = ImageDraw.Draw(img)
            draw.rectangle([4, 4, 11, 11], fill='#ffffff')
            
            self._tray = pystray.Icon("aicp", img, "AICP Engine", self._build_menu())
        except ImportError:
            pass
    
    def _build_menu(self):
        import pystray
        
        menu_items = []
        
        # 默认：打开 Workbench
        menu_items.append(pystray.MenuItem(
            "打开 Workbench",
            lambda: webbrowser.open("http://127.0.0.1:9000"),
            enabled=True,
        ))
        menu_items.append(pystray.Menu.SEPARATOR)
        
        # 分离应用菜单和系统菜单
        app_menus = []
        system_menus = []
        
        for label, items in self._menu_items.items():
            sub_items = []
            for item in items:
                lbl = item.get("label", "")
                action = item.get("action", "")
                receiver = item.get("receiver", "")
                item_payload = item.get("payload", {})
                
                menu_id = f"{label}:{action}"
                self._app_callbacks[menu_id] = {
                    "receiver": receiver,
                    "payload": {**item_payload, "action": action},
                }
                
                def make_callback(mid=menu_id):
                    return lambda: self._on_app_action(mid)
                
                sub_items.append(pystray.MenuItem(lbl, make_callback(), enabled=True))
            
            # 判断是应用还是系统菜单
            # applications 的 receiver 以 "applications/" 开头
            is_app = any(
                item.get("receiver", "").startswith("applications/")
                for item in items
            )
            
            if is_app:
                app_menus.append(pystray.MenuItem(label, pystray.Menu(*sub_items)))
            else:
                system_menus.append(pystray.MenuItem(label, pystray.Menu(*sub_items)))
        
        # 应用菜单
        for m in app_menus:
            menu_items.append(m)
        
        if app_menus and system_menus:
            menu_items.append(pystray.Menu.SEPARATOR)
        
        # 系统菜单
        for m in system_menus:
            menu_items.append(m)
        
        if self._menu_items:
            menu_items.append(pystray.Menu.SEPARATOR)
        
        menu_items.append(pystray.MenuItem("重启引擎", self._on_restart))
        menu_items.append(pystray.MenuItem("退出系统", self._on_exit))
        
        return pystray.Menu(*menu_items)
    
    def _rebuild_menu(self):
        if self._tray:
            self._tray.menu = self._build_menu()
    
    def _on_app_action(self, menu_id):
        info = self._app_callbacks.get(menu_id)
        if not info:
            return
        
        receiver = info["receiver"]
        payload = info["payload"]
        print(f"[Tray] Menu clicked: receiver={receiver}, payload={payload}") 
        async def notify():
            import aiohttp
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        f"http://127.0.0.1:9000/api/{receiver}",
                        json={"payload": payload},
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
            except Exception:
                pass
        
        asyncio.run_coroutine_threadsafe(notify(), self.loop)
    
    def _on_restart(self):
        async def restart():
            import aiohttp
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        "http://127.0.0.1:9000/api/os/_gateway",
                        json={"payload": {"action": "STOP"}},
                        timeout=aiohttp.ClientTimeout(total=5),
                    )
            except Exception:
                pass
            import os, sys
            os.execv(sys.executable, [sys.executable] + sys.argv)
        
        asyncio.run_coroutine_threadsafe(restart(), self.loop)
    
    def _on_exit(self):
        if self._tray:
            self._tray.stop()
        if hasattr(self, '_root'):
            self._root.after(100, self._root.destroy)
        if self._on_shutdown:
            self._on_shutdown()
    
    def add_menu(self, app_id, label, items):
        self._menu_items[label] = items
        self._rebuild_menu()
    
    def remove_menu(self, app_id):
        for label, items in list(self._menu_items.items()):
            for item in items:
                if item.get("receiver", "").startswith(app_id):
                    del self._menu_items[label]
                    break
        
        for mid in list(self._app_callbacks.keys()):
            if any(mid.startswith(f"{label}:") for label in self._menu_items):
                continue
            if mid.startswith(f"{app_id}:") or any(
                mid.startswith(f"{label}:") for label in list(self._menu_items.keys())
                if label.startswith(app_id)
            ):
                del self._app_callbacks[mid]
        
        self._rebuild_menu()
    
    def list_menus(self):
        return {
            "menus": dict(self._menu_items),
            "callbacks": dict(self._app_callbacks),
        }


import core


async def execute(envelop, agent):
    global _tray_instance
    
    action = envelop.payload.get("action", "")
    
    if _tray_instance is None:
        _tray_instance = TrayManager(
            agent.loop,
            on_shutdown=agent.system.on_shutdown if hasattr(agent.system, 'on_shutdown') else None,
        )

        _tray_instance.start()
    
    if action == "add_menu":
        app_id = envelop.payload.get("app_id", "")
        label = envelop.payload.get("label", app_id)
        items = envelop.payload.get("items", [])
        
        if not app_id or not items:
            envelop.payload = {"error": "app_id and items required"}
            return envelop
        
        _tray_instance.add_menu(app_id, label, items)
        envelop.payload = {"ok": True, "app_id": app_id, "label": label}
        return envelop
    
    elif action == "remove_menu":
        app_id = envelop.payload.get("app_id", "")
        if not app_id:
            envelop.payload = {"error": "app_id required"}
            return envelop
        _tray_instance.remove_menu(app_id)
        envelop.payload = {"ok": True, "app_id": app_id}
        return envelop
    
    elif action == "list":
        envelop.payload = _tray_instance.list_menus()
        return envelop
    
    elif action == "restart":
        _tray_instance._on_restart()
        envelop.payload = {"ok": True}
        return envelop
    
    elif action == "exit":
        _tray_instance._on_exit()
        envelop.payload = {"ok": True}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop
