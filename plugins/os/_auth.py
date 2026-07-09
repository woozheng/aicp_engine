"""认证插件 — 协议 v3.0 系统插件
支持 enable_auth 开关，关闭时所有请求放行。
开启时验证 X-AICP-Token / Authorization Bearer / Cookie。
"""
import core


async def execute(envelop, agent):
    action = envelop.payload.get("action", "verify")
    
    if action == "verify":
        return await _verify(envelop, agent)
    elif action == "login":
        return await _login(envelop, agent)
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop


async def _verify(envelop, agent):
    # 读取配置
    enable_auth = agent.config.get("enable_auth", False)
    if not enable_auth:
        envelop.payload = {"ok": True, "message": "Auth disabled"}
        return envelop
    
    route = envelop.payload.get("route", envelop.receiver)
    
    # 公开路由
    public_routes = { "auth/login"}
    if route in public_routes:
        envelop.payload = {"ok": True}
        return envelop
    
    # 提取 token
    token = envelop.meta.get("token", "")
    if not token:
        auth_header = envelop.meta.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        token = envelop.meta.get("cookie_token", "")
    
    # 验证
    tokens = set(agent.config.get("tokens", []))
    if token and token in tokens:
        envelop.payload = {"ok": True}
        return envelop
    
    envelop.payload = {"ok": False, "error": "Unauthorized"}
    return envelop


async def _login(envelop, agent):
    username = envelop.payload.get("username", "")
    password = envelop.payload.get("password", "")
    
    users = agent.config.get("users", {})
    if username in users and users[username] == password:
        token = f"tok_{username}"
        envelop.payload = {"ok": True, "token": token}
    else:
        envelop.payload = {"ok": False, "error": "Invalid credentials"}
    
    return envelop