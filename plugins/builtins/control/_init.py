"""控制面板入口"""
import core
import webbrowser


async def execute(envelop, agent):
    payload = envelop.payload.get("payload", envelop.payload)
    action = payload.get("action", "init")

    if action == "init":
        await agent.system.call(core.Envelop(
            sender="builtins/control/_init",
            receiver="shell/_tray",
            payload={
                "action": "add_menu",
                "app_id": "control",
                "label": "⚙️ 控制面板",
                "items": [
                    {"label": "打开控制面板", "action": "open_web", "url": "/control/index.html", "receiver": "builtins/control/_init"},
                ],
            },
        ))
        envelop.payload = {"ok": True}
        return envelop

    elif action == "open_web":
        webbrowser.open(f"{agent.base_url}/control/index.html")
        return envelop

    envelop.payload = {"error": "Unknown action"}
    return envelop