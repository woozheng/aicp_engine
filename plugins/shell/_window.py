"""窗口管理插件 — 协议 v3.0 系统插件
获取活动窗口信息、窗口列表。
"""
import core


async def execute(envelop, agent):
    action = envelop.payload.get("action", "active")
    
    if action == "active":
        try:
            import pygetwindow as gw
            win = gw.getActiveWindow()
            if win:
                envelop.payload = {
                    "title": win.title,
                    "x": win.left,
                    "y": win.top,
                    "width": win.width,
                    "height": win.height,
                    "visible": win.visible,
                }
            else:
                envelop.payload = {"error": "No active window"}
        except ImportError:
            # Windows 回退
            import subprocess
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-Process | Where-Object {$_.MainWindowTitle -ne ''}).MainWindowTitle"],
                    capture_output=True, text=True,
                )
                titles = [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]
                envelop.payload = {"active_windows": titles}
            except Exception as e:
                envelop.payload = {"error": str(e)}
        
        return envelop
    
    elif action == "list":
        try:
            import pygetwindow as gw
            windows = []
            for win in gw.getAllWindows():
                if win.title:
                    windows.append({
                        "title": win.title,
                        "x": win.left,
                        "y": win.top,
                        "width": win.width,
                        "height": win.height,
                        "visible": win.visible,
                    })
            envelop.payload = {"windows": windows}
        except Exception as e:
            envelop.payload = {"error": str(e)}
        
        return envelop
    
    elif action == "focus":
        title = envelop.payload.get("title", "")
        if not title:
            envelop.payload = {"error": "title required"}
            return envelop
        
        try:
            import pygetwindow as gw
            for win in gw.getWindowsWithTitle(title):
                win.activate()
                envelop.payload = {"ok": True, "title": win.title}
                return envelop
            envelop.payload = {"error": f"Window not found: {title}"}
        except Exception as e:
            envelop.payload = {"error": str(e)}
        
        return envelop
    
    elif action == "move":
        title = envelop.payload.get("title", "")
        x = envelop.payload.get("x", 0)
        y = envelop.payload.get("y", 0)
        width = envelop.payload.get("width")
        height = envelop.payload.get("height")
        
        if not title:
            envelop.payload = {"error": "title required"}
            return envelop
        
        try:
            import pygetwindow as gw
            for win in gw.getWindowsWithTitle(title):
                if width and height:
                    win.moveTo(x, y)
                    win.resizeTo(width, height)
                else:
                    win.moveTo(x, y)
                envelop.payload = {"ok": True, "title": win.title}
                return envelop
            envelop.payload = {"error": f"Window not found: {title}"}
        except Exception as e:
            envelop.payload = {"error": str(e)}
        
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop