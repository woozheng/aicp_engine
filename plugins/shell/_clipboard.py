"""剪贴板插件 — 协议 v3.0 系统插件
读写系统剪贴板。
"""
import core


async def execute(envelop, agent):
    action = envelop.payload.get("action", "read")
    
    if action == "read":
        try:
            import pyperclip
            content = pyperclip.paste()
            envelop.payload = {"content": content}
        except ImportError:
            # Windows 回退
            import subprocess
            try:
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Clipboard"],
                    capture_output=True, text=True,
                )
                content = result.stdout.strip()
                envelop.payload = {"content": content}
            except Exception as e:
                envelop.payload = {"error": f"Clipboard read failed: {e}"}
        
        return envelop
    
    elif action == "write":
        content = envelop.payload.get("content", "")
        if not content:
            envelop.payload = {"error": "No content"}
            return envelop
        
        try:
            import pyperclip
            pyperclip.copy(content)
            envelop.payload = {"ok": True}
        except ImportError:
            import subprocess
            try:
                subprocess.run(
                    ["powershell", "-Command", f"Set-Clipboard -Value '{content}'"],
                    capture_output=True,
                )
                envelop.payload = {"ok": True}
            except Exception as e:
                envelop.payload = {"error": f"Clipboard write failed: {e}"}
        
        return envelop
    
    elif action == "clear":
        try:
            import pyperclip
            pyperclip.copy("")
        except ImportError:
            import subprocess
            subprocess.run(["powershell", "-Command", "Set-Clipboard -Value ''"])
        
        envelop.payload = {"ok": True}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop