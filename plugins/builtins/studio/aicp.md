# AICP Plugin Protocol v5.3 — Complete Capability Reference + Development Specification

---

## ⛔ STOP — READ ONLY ⛔

You are receiving the AICP protocol for the FIRST time.
Read silently. Do NOT output any code. Do NOT generate plugins.
Reply EXACTLY: "Protocol understood. Ready for requirements design."
Then WAIT for the user's first requirement before writing any code.

---

# PART 0: AICP Core Concepts

## Three Atomic Units

- **Envelop** — The only data carrier. Structure: `{sender, receiver, intent, payload, trace_id, message_id, channel_id, ttl, meta}`
  - Plugins may only read and write `payload` and `meta`
  - Setting `receiver` routes to the next plugin
  - Return `None` to terminate the flow
- **Plugin** — Processing function. Signature: `async def execute(envelop, agent) -> Envelop | None`
- **Agent** — Capability container. Injected by the engine. Plugins can mount new capabilities onto it.

## System Architecture

| Port | Service | Plugin | Description |
|------|---------|--------|-------------|
| `port` | HTTP API + Static | `os/_gateway` | JSON API, static files, Envelop routing |
| `port + 1` | WebSocket | `os/_websocket` | Real-time bidirectional communication |
| `port + 2` | File Upload | `os/_file_receiver` | Multipart file upload |

## Agent Capabilities

| Tool | Returns | Description |
|------|---------|-------------|
| `agent.llm.chat(messages)` | `str` | Call LLM |
| `agent.llm.chat_json(messages)` | `dict` | Call LLM, return parsed JSON |
| `agent.llm.chat_stream(messages)` | `AsyncIterator[str]` | Streaming LLM call |
| `agent.system.show_bubble(options)` | `dict` | Show bubble notification |
| `agent.system.show_result_card(options)` | `dict` | Show result card |
| `agent.system.call(envelop)` | `Envelop` | Cross-plugin communication |
| `agent.scheduler.create_timer(seconds, callback)` | `timer_id` | Create timer |

## Base Properties

| Property | Description |
|----------|-------------|
| `agent.config` | System config dict |
| `agent.log` | Logger object |
| `agent.data_dir` | Path to data directory |
| `agent.base_url` | Base URL of engine |

---

# PART 1: Plugin Patterns

## Pattern 1: Standard (chat, Q&A, summarize)

```python
async def execute(envelop, agent):
    llm = agent.llm
    if not llm:
        envelop.payload = {"error": "LLM not available"}
        return envelop

    result = await llm.chat([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": envelop.payload.get("content", "")}
    ])
    envelop.payload = {"result": result.strip()}
    return envelop
```

## Pattern 2: Pipeline (analyze → process)

```python
async def execute(envelop, agent):
    llm = agent.llm
    text = envelop.payload.get("content", "")

    keywords = await llm.chat_json([
        {"role": "system", "content": "Extract keywords. Return JSON: {keywords: [...]}"},
        {"role": "user", "content": text}
    ])

    summary = await llm.chat_json([
        {"role": "system", "content": "Summarize based on keywords."},
        {"role": "user", "content": str(keywords)}
    ])

    envelop.payload = {"keywords": keywords, "summary": summary}
    return envelop
```

## Pattern 3: Parallel (multi-expert)

```python
import asyncio

async def execute(envelop, agent):
    llm = agent.llm
    task = envelop.payload.get("content", "")

    async def expert(i):
        return await llm.chat([
            {"role": "system", "content": f"You are expert {i+1}."},
            {"role": "user", "content": task}
        ])

    results = await asyncio.gather(*[expert(i) for i in range(3)])
    envelop.payload = {"results": results}
    return envelop
```

## Pattern 4: Multi-Action (chat with history)

```python
async def execute(envelop, agent):
    action = envelop.payload.get("action")

    if action == "send":
        user_message = envelop.payload.get("message", "")
        history = envelop.payload.get("history", [])
        system_prompt = envelop.payload.get("system_prompt", "You are a helpful assistant.")
        
        messages = [{"role": "system", "content": system_prompt}] + history
        messages.append({"role": "user", "content": user_message})

        reply = await agent.llm.chat(messages)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})

        envelop.payload = {"reply": reply, "history": history}
        return envelop

    elif action == "reset":
        envelop.payload = {"success": True, "history": []}
        return envelop

    envelop.payload = {"error": "Unknown action"}
    return envelop
```

## Pattern 5: No-LLM (webhook, proxy)

```python
async def execute(envelop, agent):
    data = envelop.payload.get("content", envelop.payload)
    envelop.payload = {"received": data, "status": "ok"}
    return envelop
```

## Pattern 6: Multi-File (complex systems)

When a system has multiple responsibilities, split into multiple files:

```python
# init.py — entry point
async def execute(envelop, agent):
    action = envelop.payload.get("action")
    if action == "tick":
        envelop.receiver = f"applications/{PROJECT}/tick"
        return envelop
    elif action == "status":
        envelop.receiver = f"applications/{PROJECT}/status"
        return envelop
```

## Pattern 7: Streaming

```python
async def execute(envelop, agent):
    stream = agent.llm.chat_stream([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": envelop.payload.get("content", "")}
    ])

    async def sse_generator():
        async for chunk in stream:
            if chunk:
                yield chunk
        yield "[DONE]"

    envelop.payload = sse_generator()
    return envelop
```

## Pattern 8: File Upload

```python
# Step 1: Frontend upload to /upload
# Step 2: Pass file_path via Envelop
result = await agent.system.call(core.Envelop(
    sender=f"applications/{PROJECT}/processor",
    receiver="applications/{PROJECT}/analyzer",
    payload={"file_path": file_path, "action": "analyze"}
))
```

---

# PART 2: Critical Rules

## Plugin Signature

```python
async def execute(envelop, agent) -> envelop | None
envelop.payload is a dict — read and write state here

Return None to discard the envelop
```

## Import Rules

- **NO imports of AICP modules** — never `from aicp import ...`
- `envelop` and `agent` are injected by the engine

## LLM Usage

- `agent.llm.chat(messages)` returns a plain str, NOT a dict
- Always check `if not agent.llm:` before calling

## Routing Rules

- Do NOT set `envelop.receiver` unless routing to ANOTHER plugin
- Setting it to yourself = infinite loop

## Error Handling

- Do NOT add defensive wrappers — the engine handles timeouts
- NO `asyncio.wait_for` / manual timeout

## File Transfer Rules

- Files >1MB: Use `os/_file_receiver` (port+2) multipart upload
- NEVER send >5MB through gateway JSON API

## Static Content Serving

```python
with open(filepath, "rb") as f:
    envelop.meta["static_content"] = f.read()
envelop.meta["content_type"] = "image/jpeg"
return envelop
```

---

# PART 3: _init.py Template (REQUIRED)

```python
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
                "label": "App Name",
                "items": [
                    {"label": "Open Panel", "action": "open_web", "url": f"/{PROJECT}/index.html", "receiver": f"applications/{PROJECT}/_init"},
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
```

**Key rules:**

- `PROJECT = Path(__file__).parent.name` — NEVER hardcode project name
- All receivers use `f"applications/{PROJECT}/..."`
- Menu items point to handler plugins

---

# PART 4: Output Format

## Path Placeholder

ALL paths use `{project}` placeholder — NEVER write your own project name.

## Code Block Wrapper

```
=== PLUGIN: plugins/applications/{project}/chat.py ===
(code)
=== END ===

=== HTML: www/{project}/index.html ===
(code)
=== END ===

=== APP: plugins/applications/{project}/app.yaml ===
(yaml)
=== END ===
```

| Prefix | Path |
|--------|------|
| PLUGIN | `plugins/applications/{project}/` |
| HTML | `www/{project}/` |


- Blocks **MUST** use `===` markers
- Multiple PLUGIN blocks for multi-file projects

## Plugin File Naming

- Descriptive names: `chat.py`, `tick.py`, `assign.py`
- **NEVER** `__init__.py` — it is skipped by the engine

---

# PART 5: Frontend Rules (MUST FOLLOW)

## Core Rules

```javascript
// 1. 项目名动态获取 - NEVER hardcode
const project = window.location.pathname.split('/')[1];

// 2. API 路径 - NEVER hardcode port or hostname
const API = `/api/applications/${project}`;

// 3. WebSocket 地址 - ALWAYS get from gateway
// ⚠️ 警告：此接口为 GET，不是 POST！
// 不要加 method、headers、body —— 加了就错！
const { url } = await fetch('/api/ws_config').then(r => r.json());
const ws = new WebSocket(`${url}?channel=${project}_dashboard`);

// 4. 文件上传 - use relative path
const p = window.location.port;
const uploadUrl = p ? `${window.location.protocol}//${window.location.hostname}:${+p + 2}/upload` : '/upload';
const res = await fetch(uploadUrl, { method: 'POST', body: formData });

```

## Complete Template

```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>App Name</title>
    <style>
        body { font-family: system-ui, sans-serif; background: #fff; }
        /* No gradients, no shadows */
    </style>
</head>
<body>
    <div id="app"></div>
    <script>
        (function() {
            const project = window.location.pathname.split('/')[1];
            const API = `/api/applications/${project}`;
            
            // WebSocket
            let ws = null;
            async function connectWS() {
                const { url } = await fetch('/api/ws_config').then(r => r.json());
                ws = new WebSocket(`${url}?channel=${project}_dashboard`);
                ws.onmessage = (e) => {
                    const data = JSON.parse(e.data);
                    if (data.type === 'status') render(data.snapshot);
                };
                ws.onclose = () => setTimeout(connectWS, 5000);
            }
            
            // Load data
            async function load() {
                const res = await fetch(`${API}/scheduler`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'get_snapshot' })
                });
                const data = await res.json();
                if (data.snapshot) render(data.snapshot);
            }
            
            function render(snapshot) {
                // Render using snapshot data
            }
            
            load();
            connectWS();
        })();
    </script>
</body>
</html>
```

## Checklist

- [ ] Project name from `window.location.pathname.split('/')[1]`
- [ ] API uses `/api/applications/${project}`
- [ ] No hardcoded ports (9000, 9001, 9002)
- [ ] No hardcoded hostnames (localhost, 127.0.0.1)
- [ ] WebSocket uses `/api/ws_config`  ,Get, not use Post
- [ ] File upload uses `/upload`
- [ ] No gradients, no shadows in CSS

---

# PART 6: Snapshot & Data Consistency

## Single Source of Truth

```python
def build_snapshot(account, stock_data, trades_today, analysis):
    return {
        "time": get_now_str(),
        "account": {
            "cash": account["cash"],
            "market_value": total - cash,
            "total": total,
            "profit": profit
        },
        "holdings": account.get("holdings", {}),
        "quotes": stock_data,
        "today_trades": trades_today,
        "analysis": analysis
    }
```

### DO

- Build fresh snapshot for each decision cycle
- Use same data for AI and frontend
- Save snapshot once per cycle
- Push snapshot via WebSocket

### DO NOT

- Save account in multiple places
- Use different data for AI and frontend
- Cache stock_data across cycles

---

# PART 7: Common AI Mistakes

### ❌ Hardcoding project name

```python
# WRONG
receiver = "applications/stockai/trader"
```

```python
# CORRECT
PROJECT = Path(__file__).parent.name
receiver = f"applications/{PROJECT}/trader"
```

### ❌ Hardcoding WebSocket port

```javascript
// WRONG
const ws = new WebSocket('ws://localhost:9001/ws');
```

```javascript
// CORRECT
const { url } = await fetch('/api/ws_config').then(r => r.json());
const ws = new WebSocket(`${url}?channel=${project}_dashboard`);
```

### ❌ Hardcoding API URL

```javascript
// WRONG
fetch('/api/applications/stockai/trader')
```

```javascript
// CORRECT
const project = window.location.pathname.split('/')[1];
fetch(`/api/applications/${project}/trader`)
```

### ❌ Encoding large files in JSON

```python
# WRONG
envelop.payload = {"video_base64": base64_string}
```

```python
# CORRECT
envelop.payload = {"video_path": "/path/to/file"}
```

### ❌ Using different data for AI and frontend

```python
# WRONG
snapshot_for_ai = get_snapshot()
snapshot_for_frontend = get_different_snapshot()
```

```python
# CORRECT
snapshot = build_snapshot(account, stock_data, trades, analysis)
# Use same snapshot for both
```

---

# PART 8: Checklist (Before Output)

- [ ] `_init.py` created with menu registration
- [ ] Uses `PROJECT = Path(__file__).parent.name`
- [ ] No `from aicp import ...`
- [ ] Checked `if not agent.llm:` before calling
- [ ] `agent.llm.chat()` returns str, NOT dict
- [ ] No `envelop.receiver = self`
- [ ] No `asyncio.wait_for` / manual timeout
- [ ] ALL paths use `{project}` placeholder
- [ ] Output: `=== PLUGIN/HTML/APP: path ===` ... `=== END ===`
- [ ] Frontend: project from `window.location.pathname.split('/')[1]`
- [ ] Frontend: API uses `/api/applications/${project}`
- [ ] Frontend: WebSocket uses `/api/ws_config`
- [ ] Frontend: No hardcoded ports or hostnames
- [ ] Frontend: No gradients, no shadows
- [ ] Snapshot used as single source of truth

[DONE]