"""屏幕插件 — 协议 v3.0 系统插件
获取屏幕信息、截图。
"""
import core


async def execute(envelop, agent):
    action = envelop.payload.get("action", "info")
    
    if action == "info":
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            w = root.winfo_screenwidth()
            h = root.winfo_screenheight()
            root.destroy()
            envelop.payload = {"width": w, "height": h}
        except Exception:
            # 尝试 pyautogui
            try:
                import pyautogui
                size = pyautogui.size()
                envelop.payload = {"width": size.width, "height": size.height}
            except Exception as e:
                envelop.payload = {"error": f"Cannot get screen info: {e}"}
        
        return envelop
    
    elif action == "screenshot":
        filename = envelop.payload.get("filename", "screenshot.png")
        region = envelop.payload.get("region", None)  # (x, y, w, h)
        
        data_dir = agent.data_dir if hasattr(agent, 'data_dir') else __import__('pathlib').Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        
        if ".." in filename or filename.startswith("/"):
            envelop.payload = {"error": "Forbidden"}
            return envelop
        
        filepath = data_dir / filename
        
        try:
            import pyautogui
            if region:
                img = pyautogui.screenshot(region=tuple(region))
            else:
                img = pyautogui.screenshot()
            img.save(str(filepath))
            envelop.payload = {"ok": True, "filepath": str(filepath), "size": img.size}
        except ImportError:
            # Pillow 回退
            try:
                from PIL import ImageGrab
                if region:
                    img = ImageGrab.grab(bbox=tuple(region))
                else:
                    img = ImageGrab.grab()
                img.save(str(filepath))
                envelop.payload = {"ok": True, "filepath": str(filepath), "size": img.size}
            except Exception as e:
                envelop.payload = {"error": f"Screenshot failed: {e}"}
        except Exception as e:
            envelop.payload = {"error": f"Screenshot failed: {e}"}
        
        return envelop
    
    elif action == "pixel":
        x = envelop.payload.get("x", 0)
        y = envelop.payload.get("y", 0)
        
        try:
            import pyautogui
            color = pyautogui.pixel(x, y)
            envelop.payload = {"x": x, "y": y, "color": color}
        except Exception as e:
            envelop.payload = {"error": str(e)}
        
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop