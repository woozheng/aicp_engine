# plugins/builtins/notify/email_notify.py
"""邮件发送通知"""
import core
from plugins.builtins.agents.config import get_channel_config


async def send(agent, to: str, subject: str, body: str, attachments: list = None) -> bool:
    cfg = get_channel_config("email")
    if not cfg.get("enabled") or not cfg.get("smtp_host"):
        print("[email_notify] 邮箱未启用")
        return False
    
    try:
        result = await agent.system.call(core.Envelop(
            sender="builtins/notify/email",
            receiver="auto/mail_handler",
            payload={
                "action": "send",
                "params": {
                    "smtp_host": cfg["smtp_host"],
                    "smtp_port": cfg["smtp_port"],
                    "user": cfg["user"],
                    "password": cfg["password"],
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "attachments": attachments or []
                }
            }
        ))
        ok = result and result.payload.get("ok", False)
        print(f"[email_notify] {'✅' if ok else '❌'} 发送: {subject[:30]}")
        return ok
    except Exception as e:
        print(f"[email_notify] 发送异常: {e}")
        return False