import core
import webbrowser
from pathlib import Path

PROJECT = Path(__file__).parent.name

async def execute(envelop, agent):
    payload = envelop.payload.get("payload", envelop.payload)
    action = payload.get("action", "init")

    if action == "init":
        await agent.system.call(core.Envelop(
            sender=f"applications/{PROJECT}/_init",
            receiver="shell/_tray",
            payload={
                "action": "add_menu",
                "app_id": PROJECT,
                "label": "📸 视觉知识库",
                "items": [
                    # {"label": "📥 上传截图", "action": "open_web", "url": f"/{PROJECT}/index.html", "receiver": f"applications/{PROJECT}/_init"},
                    {"label": "📂 打开视觉库", "action": "open_web", "url": f"/{PROJECT}/browse.html", "receiver": f"applications/{PROJECT}/_init"},
                ],
            },
        ))
        envelop.payload = {"ok": True}
        return envelop

    elif action == "open_web":
        webbrowser.open(f"{agent.base_url}/{PROJECT}/index.html")
        return envelop

    else:
        envelop.payload = {"error": f"Unknown action: {action}"}
        return envelop