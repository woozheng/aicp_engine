# plugins/auto/mail_handler.py
import smtplib
import imaplib
from pathlib import Path
import email as email_lib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.policy import default
from datetime import datetime

def help():
    return {
        "actions": {
            "send": {
                "params": {
                    "smtp_host": "SMTP服务器",
                    "smtp_port": "SMTP端口",
                    "user": "邮箱账号",
                    "password": "授权码",
                    "to": "收件人",
                    "subject": "主题",
                    "body": "正文",
                    "attachments": "可选，附件路径列表"
                },
                "returns": {"ok": "bool"}
            },
            "check": {
                "params": {
                    "imap_host": "IMAP服务器",
                    "imap_port": "IMAP端口",
                    "user": "邮箱账号",
                    "password": "授权码"
                },
                "returns": {"mails": "未读邮件列表"}
            }
        }
    }

async def execute(envelop, agent):
    action = envelop.payload.get("action")
    params = envelop.payload.get("params", {})

    if action == "send":
        return await _send(envelop, params)
    elif action == "check":
        return await _check(envelop, params)
    
    envelop.payload = {"ok": False, "error": f"不支持: {action}"}
    return envelop

async def _send(envelop, params):
    import mimetypes
    from email.header import Header
    from email.mime.base import MIMEBase
    import email as email_lib
    
    try:
        msg = MIMEMultipart()
        msg["From"] = params["user"]
        msg["To"] = params["to"]
        msg["Subject"] = params["subject"]
        msg.attach(MIMEText(params["body"], "plain", "utf-8"))

        for path in params.get("attachments", []):
            if not path:
                continue
            filepath = Path(path)
            if not filepath.exists():
                continue
            
            with open(filepath, "rb") as f:
                file_data = f.read()
            
            # 根据文件后缀设置正确的 MIME 类型
            mime_type, _ = mimetypes.guess_type(filepath.name)
            if mime_type:
                main_type, sub_type = mime_type.split("/", 1)
            else:
                main_type, sub_type = "application", "octet-stream"
            
            # 用 MIMEBase，文件名用 Header 处理中文
            part = MIMEBase(main_type, sub_type)
            part.set_payload(file_data)
            email_lib.encoders.encode_base64(part)
            
            encoded_filename = Header(filepath.name, "utf-8").encode()
            part.add_header("Content-Disposition", "attachment", filename=encoded_filename)
            
            msg.attach(part)

        # 根据端口选择连接方式
        port = params.get("smtp_port", 587)
        if port == 465:
            with smtplib.SMTP_SSL(params["smtp_host"], port, timeout=10) as s:
                s.login(params["user"], params["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(params["smtp_host"], port, timeout=10) as s:
                s.starttls()
                s.login(params["user"], params["password"])
                s.send_message(msg)

        envelop.payload = {"ok": True, "data": {"sent": True}}
        return envelop
        
    except Exception as e:
        envelop.payload = {"ok": False, "error": str(e)}
        return envelop

async def _check(envelop, params):
    try:
        imap = imaplib.IMAP4_SSL(params["imap_host"], params["imap_port"])
        imap.login(params["user"], params["password"])
        imap.select("INBOX")
        _, nums = imap.search(None, "UNSEEN")
        mails = []
        for num in nums[0].split()[-10:]:
            _, data = imap.fetch(num, "(RFC822)")
            msg = email_lib.message_from_bytes(data[0][1], policy=default)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            mails.append({"from": msg["From"], "subject": msg["Subject"], "body": body})
        imap.logout()
        envelop.payload = {"ok": True, "data": {"mails": mails}}
    except Exception as e:
        envelop.payload = {"ok": False, "error": str(e)}
    return envelop