"""HTTP 入口插件 — 协议 v3.0 系统插件"""
from aiohttp import web
from inspect import isasyncgen
import core
from pathlib import Path
import json
import asyncio
from datetime import datetime


async def execute(envelop, agent):
    action = envelop.payload.get("action", "START")
    
    if action == "START":
        port = envelop.payload.get("port", 9000)
        host = envelop.payload.get("host", "127.0.0.1")
        
        app = web.Application()
        
        @web.middleware
        async def cors(request, handler):
            if request.method == "OPTIONS":
                return web.Response(status=200, headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-AICP-Token, Authorization",
                })
            resp = await handler(request)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        
        app.middlewares.append(cors)
        
        # ============================================================
        # 🔥 配置加载（放在最前面）
        # ============================================================
        async def _load_kb_config(agent):
            """加载知识库配置"""
            try:
                result = await agent.system.call(core.Envelop(
                    sender="os/_gateway",
                    receiver="builtins/knowledge_base/main",
                    payload={"action": "config_get"}
                ))
                if result and result.payload:
                    return result.payload.get("config", {})
            except Exception:
                pass
            return {}

        async def _save_to_kb_async(agent, content, source, title, payload):
            """异步存入知识库"""
            try:
                if not hasattr(agent, 'system') or not hasattr(agent.system, 'call'):
                    return
                
                if len(content) > 100000:
                    content = content[:100000] + "\n\n... (已截断)"
                
                await agent.system.call(core.Envelop(
                    sender="os/_gateway",
                    receiver="builtins/knowledge_base/main",
                    payload={
                        "action": "auto_save",
                        "params": {
                            "content": content,
                            "source": source,
                            "title": title[:200],
                            "type": "api_response",
                            "metadata": {
                                "path": source,
                                "from_api": True,
                                "timestamp": datetime.now().isoformat()
                            }
                        }
                    }
                ))
            except Exception:
                pass

        # ============================================================
        # 自动捕获知识库
        # ============================================================
        async def _auto_capture_to_kb(result, request, agent):
            """自动捕获 API 响应到知识库"""
            try:
                payload = result.payload
                if not payload or not isinstance(payload, dict):
                    return
                
                path = request.path
                
                # 第 1 层：路由白名单
                config = await _load_kb_config(agent)
                auto_cfg = config.get("auto_save", {})
                
                if not auto_cfg.get("enabled", True):
                    return
                
                allowed_routes = auto_cfg.get("allowed_routes", [])
                if allowed_routes:
                    if not any(path.startswith(route) for route in allowed_routes):
                        return
                
                # 第 2 层：排除知识库自身
                if path.startswith("/api/builtins/knowledge_base"):
                    return
                
                # 第 3 层：排除系统路径
                exclude_paths = [
                    "/api/os/_gateway",
                    "/api/ws_config",
                    "/api/list",
                    "/health"
                ]
                for p in exclude_paths:
                    if path.startswith(p):
                        return
                
                # 第 4 层：排除查询/状态类 action
                action = payload.get("action", "")
                exclude_actions = ["search", "list", "status", "ping", "health", "stats"]
                if action in exclude_actions:
                    return
                
                # 第 5 层：提取内容
                content = None
                for key in ["result", "data", "content", "reply", "response", "answer"]:
                    if key in payload:
                        val = payload.get(key)
                        if val:
                            content = val
                            break
                
                if isinstance(content, (dict, list)):
                    content = json.dumps(content, ensure_ascii=False, indent=2)
                
                if not content or len(str(content)) < 50:
                    return
                
                # 第 6 层：标题
                title = f"{path.split('/')[-1]}_{datetime.now().strftime('%Y%m%d')}"
                if payload.get("title"):
                    title = payload["title"]
                elif payload.get("name"):
                    title = payload["name"]
                
                # 第 7 层：异步存入
                try:
                    asyncio.create_task(_save_to_kb_async(
                        agent, str(content), path, title, payload
                    ))
                except Exception:
                    pass
                    
            except Exception:
                pass

        # ============================================================
        # handle_api
        # ============================================================
        async def handle_api(request):
            """POST /api/{path}"""
            path = request.match_info["path"]
            agent.log.info(f"[Gateway] POST /api/{path}")
            
            # WebSocket 配置
            if path == "ws_config":
                # ... 不变 ...
                pass
            
            # 认证
            if path == "auth/verify":
                token = request.headers.get("X-AICP-Token", "")
                if not token:
                    return web.json_response({"ok": False, "error": "missing token"}, status=401)
                
                # 调用 os/_auth 验证这个 token 是否有效
                auth_env = core.Envelop(
                    sender="os/_gateway",
                    receiver="os/_auth",
                    payload={"action": "verify", "route": path},
                    meta={
                        "token": token,
                        "authorization": request.headers.get("Authorization", ""),
                        "cookie_token": request.cookies.get("aicp_token", ""),
                    },
                )
                auth_result = await agent.system.call(auth_env)
                
                if auth_result and auth_result.payload.get("ok"):
                    return web.json_response({"ok": True})
                else:
                    return web.json_response({"ok": False}, status=401)

                    
            auth_env = core.Envelop(
                sender="os/_gateway",
                receiver="os/_auth",
                payload={"action": "verify", "route": path},
                meta={
                    "token": request.headers.get("X-AICP-Token", ""),
                    "authorization": request.headers.get("Authorization", ""),
                    "cookie_token": request.cookies.get("aicp_token", ""),
                },
            )
            auth_result = await agent.system.call(auth_env)
            if not auth_result or not auth_result.payload.get("ok"):
                return web.json_response({"error": "Unauthorized"}, status=401)
            
            # 解析请求体
            try:
                if request.can_read_body:
                    body = await request.json()
                else:
                    body = {}
            except Exception:
                body = {}
            
            # 转发到插件
            env = core.Envelop(
                sender="os/_gateway",
                receiver=path,
                intent=body.get("intent", "API_CALL"),
                payload=body.get("payload", body),
                meta={
                    **body.get("meta", {}),
                    "token": request.headers.get("X-AICP-Token", ""),
                    "authorization": request.headers.get("Authorization", ""),
                    "method": request.method,
                    "path": path,
                },
            )
            
            result = await agent.system.call(env)
            # print(f"[Gateway] result: {result}")
            # print(f"[Gateway] result.meta: {result.meta if hasattr(result, 'meta') else 'NO META'}")
            
            if result is None:
                return web.json_response({"error": "no response"}, status=500)
            

            

            # ============================================================
            # 🔥 流式响应：收集 + 捕获
            # ============================================================
            if isasyncgen(result.payload):
                collected = []
                resp = web.StreamResponse()
                resp.headers['Content-Type'] = 'text/event-stream'
                resp.headers['Cache-Control'] = 'no-cache'
                await resp.prepare(request)
                
                async for chunk in result.payload:
                    if isinstance(chunk, str):
                        collected.append(chunk)
                        await resp.write(chunk.encode())
                    elif isinstance(chunk, bytes):
                        try:
                            collected.append(chunk.decode('utf-8', errors='ignore'))
                        except:
                            collected.append(str(chunk))
                        await resp.write(chunk)
                    else:
                        collected.append(str(chunk))
                        await resp.write(str(chunk).encode())
                
                # 🔥 流式结束后捕获
                if collected:
                    full_content = ''.join(collected)
                    if len(full_content) > 50:
                        fake_result = core.Envelop(
                            sender=result.sender or "os/_gateway",
                            receiver=result.receiver or path,
                            payload={"content": full_content}
                        )
                        try:
                            await _auto_capture_to_kb(fake_result, request, agent)
                        except Exception:
                            pass
                
                return resp
            
            # ============================================================
            # 非流式响应：直接捕获
            # ============================================================
            try:
                await _auto_capture_to_kb(result, request, agent)
            except Exception:
                pass
            
            # 静态文件响应
            if result.meta.get("static_content"):
                return web.Response(
                    body=result.meta["static_content"],
                    content_type=result.meta.get("content_type", "application/octet-stream"),
                )
            
            return web.json_response(result.payload if result.payload else {"ok": True})
        
        # ============================================================
        # handle_static
        # ============================================================
        async def handle_static(request):
            """GET 请求：先查 API 路由，再查静态文件"""
            file_path = request.match_info.get("path", "")
            if not file_path:
                file_path = "index.html"
            if file_path.startswith("data/"):
                data_path = Path(file_path)
                if data_path.exists() and data_path.is_file():
                    ext = data_path.suffix.lower()
                    content_types = {
                        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".gif": "image/gif", ".svg": "image/svg+xml",
                        ".pdf": "application/pdf", ".zip": "application/zip",
                        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        ".txt": "text/plain", ".json": "application/json",
                    }
                    return web.Response(
                        body=data_path.read_bytes(),
                        content_type=content_types.get(ext, "application/octet-stream"),
                        headers={"Content-Disposition": f"inline; filename={data_path.name}"}
                    )
                return web.Response(text="Not found", status=404)
            
            if file_path == "api/ws_config":
                ws_config = agent.config.get("websocket", {})
                ws_external_url = ws_config.get("external_url", "")
                ws_port = agent.config.get("port", 9000) + 1
                
                is_secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https"
                hostname = request.host.split(':')[0]
                
                if ws_external_url:
                    url = ws_external_url
                else:
                    if is_secure:
                        url = f"wss://{hostname}/ws"
                    else:
                        url = f"ws://{hostname}:{ws_port}/ws"
                
                return web.json_response({
                    "url": url,
                    "port": ws_port,
                    "secure": is_secure
                })
            if file_path == "api/list":
                return await _route_get("os/_registry", {"action": "list"}, request)
            
            if file_path == "api/pages":
                return await _route_get("os/_static", {"action": "list"}, request)
            
            if file_path.startswith("api/"):
                route = file_path[4:]
                return await _route_get(route, {}, request)
            
            # 静态文件认证
            if file_path.endswith('.html') and file_path not in ['login.html', '404.html']:
                auth_env = core.Envelop(
                    sender="os/_gateway",
                    receiver="os/_auth",
                    payload={"action": "verify", "route": "__static__"},
                    meta={
                        "token": request.headers.get("X-AICP-Token", ""),
                        "authorization": request.headers.get("Authorization", ""),
                        "cookie_token": request.cookies.get("aicp_token", ""),
                    },
                )
                auth_result = await agent.system.call(auth_env)
                if not auth_result or not auth_result.payload.get("ok"):
                    from urllib.parse import quote
                    return web.Response(
                        text=f'<script>location.href="/login.html?redirect={quote(request.path)}"</script>',
                        content_type='text/html',
                    )
            
            # 静态文件服务
            env = core.Envelop(
                sender="os/_gateway",
                receiver="os/_static",
                intent="API_CALL",
                payload={"action": "serve", "file_path": file_path},
                meta={
                    "token": request.headers.get("X-AICP-Token", ""),
                    "method": "GET",
                    "path": file_path,
                },
            )
            
            result = await agent.system.call(env)
            
            if result and result.meta.get("static_content"):
                return web.Response(
                    body=result.meta["static_content"],
                    content_type=result.meta.get("content_type", "text/html"),
                )
            
            if result and result.payload.get("error"):
                status = 403 if "Forbidden" in str(result.payload.get("error", "")) else 404
                return web.Response(text=result.payload["error"], status=status)
            
            return web.Response(text="Not found", status=404)
        
        async def _route_get(receiver, payload, request):
            """转发 GET 请求到插件"""
            from urllib.parse import parse_qs, unquote
            import json
            from pathlib import Path

            query = parse_qs(request.query_string)
            # print(f"[Gateway] _route_get: query_string={request.query_string}")
            # print(f"[Gateway] _route_get: query={query}")
            # print(f"[Gateway] _route_get: payload before={payload}")

            # 提取 action
            if "action" in query:
                payload["action"] = query["action"][0]
                # print(f"[Gateway] _route_get: action={payload['action']}")

            # 提取 params
            if "params" in query:
                try:
                    params_str = unquote(query["params"][0])
                    # print(f"[Gateway] _route_get: params_str={params_str}")
                    params = json.loads(params_str)
                    if isinstance(params, dict):
                        payload.update(params)
                        # print(f"[Gateway] _route_get: payload after params update={payload}")
                except Exception as e:
                    print(f"[Gateway] 解析 params 失败: {e}")

            for key, values in query.items():
                if key not in ["action", "params"] and values:
                    payload[key] = values[0]

            # print(f"[Gateway] _route_get: final payload={payload}")

            env = core.Envelop(
                sender="os/_gateway",
                receiver=receiver,
                intent="API_CALL",
                payload=payload,
                meta={
                    "token": request.headers.get("X-AICP-Token", ""),
                    "method": "GET",
                },
            )
            result = await agent.system.call(env)
            if not result:
                return web.json_response({"error": "no response"}, status=500)
            
            # ============================================================
            # 🔥 文件响应（GET 请求用）
            # ============================================================
            if result.meta and result.meta.get("response_type") == "file":
                file_path = result.meta.get("file_path")
                if not file_path or not Path(file_path).exists():
                    return web.json_response({"error": "File not found"}, status=404)
                return web.FileResponse(
                    path=file_path,
                    headers={
                        "Content-Type": result.meta.get("content_type", "application/octet-stream"),
                        "Content-Disposition": f'{result.meta.get("content_disposition", "inline")}; filename="{result.meta.get("file_name", "file")}"'
                    }
                )
            
            return web.json_response(result.payload if result.payload else {"ok": True})
        
        # ========== 路由注册 ==========
        app.router.add_post("/api/{path:.*}", handle_api)
        app.router.add_get("/", handle_static)
        app.router.add_get("/{path:.*}", handle_static)
        app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
        
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        
        agent.log.info(f"[Gateway] HTTP server listening on http://{host}:{port}")
        
        envelop.payload = {"status": "listening", "port": port, "host": host}
        return envelop
    
    elif action == "STOP":
        envelop.payload = {"status": "stopped"}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop