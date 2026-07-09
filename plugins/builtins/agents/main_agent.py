# plugins/builtins/agents/main_agent.py
"""
主控制台 Agent — 所有渠道的统一入口
渠道感知 + 意图识别 + 需求精炼 + 原文保留
"""
import json
import time
import random
from pathlib import Path
import core
from plugins.builtins.message_core.core import get_core
from plugins.builtins.constants import MEMORIES_DIR

PROJECT = Path(__file__).parent.name
MEMORY_DIR = MEMORIES_DIR / "main_agent"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
MAX_HISTORY = 20

ROUTER_PROMPT = """你是 AICP 路由 Agent。

【渠道模式】
- channel: email → 不追问，缺信息用合理默认值，在 response 中注明假设
- channel: web → 可追问，缺关键信息时追问
- channel: feishu/wechat/dingtalk → 同 email

【当前状态】
- channel: {channel}
- stage: {stage}
- 已收集的需求: {requirements}
- 对话历史: {history}

【用户最新输入】
{user_input}

【你的任务】
根据 channel 模式、对话历史和用户输入，判断意图，决定下一步。

【判断规则】
1. 用户回答之前的追问 → intent = "continue_refine"
2. 用户说"刚才那个"、"再来一次" → intent = "retry"
3. 全新话题 → intent = "new_task"
4. 用户确认需求 → intent = "execute"
5. 图形界面应用 → intent = "studio"
6. 闲聊 → intent = "chat"

【分类规则】
- task：命令行任务
- studio：图形界面应用
- chat：闲聊

【精炼原则 - 关键】
- 用户原文中的文件路径、参数值、具体数字 → 必须在 document 中原样保留
- document.input = "E:\\1.png"，不是"用户提供的图片"
- document.output = "D:\\output\\result.csv"，不是"CSV文件"
- document 是用户需求的忠实转录，不要"翻译"或"概括"

【渠道适配追问策略】
- channel=web → 缺关键信息时 complete=false，追问
- 其他渠道 → complete 尽量判 true，缺信息用默认值，在 response 注明

【上传文件状态】
- 已有上传文件: {file_status}

【输出格式 - 严格 JSON】
只输出一行完整 JSON，不要 markdown 代码块包裹，不要注释，不要额外文字。
字符串内如果有换行，用 \\n 表示。如果有双引号，用 \\" 转义。

{{
  "intent": "new_task",
  "reason": "用户提出了全新的图片处理需求",
  "routing": "task",
  "file_mode": "has_path",
  "file_paths": ["E:\\1.png"],
  "complete": true,
  "document": {{
    "type": "task",
    "title": "图片灰度处理",
    "description": "把 E:\\1.png 转成黑白图片",
    "input": "E:\\1.png",
    "process": "转换为灰度",
    "output": "黑白 PNG 图片",
    "constraints": ""
  }},
  "next_question": "",
  "response": ""
}}

注意：
- document 中所有字段尽可能保留用户原文
- 非 web 渠道 complete 尽量为 true
- intent=chat 时，response 写回复内容，其他字段可为空
"""

STUDIO_MESSAGES = {
    "default": "📱 这个需求适合用 **Studio** 生成完整应用。正在为你打开...",
    "app": "📱 应用类需求推荐使用 **Studio**，正在打开...",
    "tool": "🛠️ 工具类需求推荐使用 **Studio**，正在打开..."
}


def _get_studio_message(user_input: str) -> str:
    ul = user_input.lower()
    if any(w in ul for w in ["应用", "app", "网站", "web", "管理", "系统"]):
        return STUDIO_MESSAGES["app"]
    if any(w in ul for w in ["工具", "tool", "面板", "界面"]):
        return STUDIO_MESSAGES["tool"]
    return STUDIO_MESSAGES["default"]


# ==================== 存储 ====================
def _append_history(session_id: str, role: str, content: str):
    path = MEMORY_DIR / f"{session_id}.jsonl"
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps({"role": role, "content": content, "ts": time.time()}, ensure_ascii=False) + '\n')
    except OSError:
        pass


def _load_history(session_id: str) -> list:
    path = MEMORY_DIR / f"{session_id}.jsonl"
    if not path.exists():
        return []
    history = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return history[-MAX_HISTORY:] if len(history) > MAX_HISTORY else history


def _save_state(session_id: str, stage: str, requirements: dict = None,
                task_id: str = None, uploaded_files: list = None):
    path = MEMORY_DIR / f"{session_id}_state.json"
    state = {
        "stage": stage,
        "requirements": requirements or {},
        "task_id": task_id,
        "uploaded_files": uploaded_files or []
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def _load_state(session_id: str) -> tuple:
    path = MEMORY_DIR / f"{session_id}_state.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return (
                data.get("stage", "idle"),
                data.get("requirements", {}),
                data.get("task_id"),
                data.get("uploaded_files", [])
            )
        except (json.JSONDecodeError, OSError):
            pass
    return "idle", {}, None, []


def _get_session(session_id: str) -> dict:
    stage, requirements, task_id, uploaded_files = _load_state(session_id)
    return {
        "session_id": session_id, "stage": stage,
        "history": _load_history(session_id),
        "requirements": requirements, "task_id": task_id,
        "uploaded_files": uploaded_files
    }


def _reset_session(session_id: str):
    for f in MEMORY_DIR.glob(f"{session_id}*"):
        try:
            f.unlink()
        except OSError:
            pass


# ==================== 主入口 ====================
async def execute(envelop, agent):
    print(f"[main_agent] payload keys: {list(envelop.payload.keys())}")

    session_id = envelop.payload.get("session_id", "default")
    channel = envelop.payload.get("channel", "web")
    email_sender = envelop.payload.get("_sender", "")

    if envelop.payload.get("action") == "get_session":
        _, _, _, uploaded_files = _load_state(session_id)
        envelop.payload = {"ok": True, "files": uploaded_files}
        return envelop

    if envelop.payload.get("action") == "remove_file":
        filename = envelop.payload.get("filename")
        if filename:
            stage, requirements, task_id, uploaded_files = _load_state(session_id)
            uploaded_files = [f for f in uploaded_files if f.get("name") != filename]
            _save_state(session_id, stage, requirements, task_id, uploaded_files)
            envelop.payload = {"ok": True, "files": uploaded_files}
        else:
            envelop.payload = {"ok": False, "error": "缺少 filename"}
        return envelop

    user_input = envelop.payload.get("content", "")
    new_files = envelop.payload.get("files", [])

    stage, requirements, task_id, uploaded_files = _load_state(session_id)
    uploaded_files = uploaded_files or []

    if new_files:
        uploaded_files = new_files
        _save_state(session_id, stage, requirements, task_id, uploaded_files)

    if not user_input:
        envelop.payload = {"error": "请描述你的需求"}
        return envelop

    llm = agent.llm
    if not llm:
        envelop.payload = {"error": "LLM 不可用"}
        return envelop

    session = _get_session(session_id)
    history = session.get("history", [])
    requirements = session.get("requirements", {})
    stage = session.get("stage", "idle")
    task_id = session.get("task_id")

    prompt = ROUTER_PROMPT.format(
        channel=channel,
        stage=json.dumps(stage, ensure_ascii=False),
        requirements=json.dumps(requirements, ensure_ascii=False) if requirements else "无",
        history=json.dumps(history[-10:], ensure_ascii=False),
        file_status="已有上传文件" if uploaded_files else "无上传文件",
        user_input=user_input
    )

    raw_response = await llm.chat([{"role": "user", "content": prompt}])

    import re
    cleaned = raw_response.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    match = re.search(r'\{[\s\S]*\}', cleaned)
    if match:
        cleaned = match.group()

    cleaned = re.sub(r'[\x00-\x1f\x7f]', '', cleaned)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[main_agent] JSON 解析失败: {e}")
        print(f"[main_agent] 原始返回: {raw_response[:300]}")
        result = {
            "intent": "chat",
            "routing": "chat",
            "response": raw_response[:200] or "你好！有什么可以帮你的？"
        }

    intent = result.get("intent", "chat")
    routing = result.get("routing", "chat")

    _append_history(session_id, "user", user_input)

    # ----- Chat -----
    if intent == "chat" or routing == "chat":
        response = result.get("response", "你好！有什么可以帮你的？")
        _append_history(session_id, "assistant", response)
        envelop.payload = {"type": "chat", "content": response}
        return envelop

    # ----- Studio -----
    if intent == "studio" or routing == "studio":
        response = _get_studio_message(user_input)
        _append_history(session_id, "assistant", response)
        envelop.payload = {"type": "info", "content": response, "next_action": "opening_studio"}
        await agent.system.call(core.Envelop(
            sender="builtins/agents/main_agent",
            receiver="builtins/studio/_init",
            payload={"action": "open"}
        ))
        return envelop

    # ----- Retry -----
    if intent == "retry":
        core_inst = get_core()
        if task_id and core_inst.task_exists(task_id):
            _append_history(session_id, "assistant", f"🔄 重新执行任务: {task_id}")
            return await _retry_task(envelop, agent, session_id, task_id, channel)
        else:
            response = "没有找到可以重新执行的任务，请告诉我具体需求。"
            _append_history(session_id, "assistant", response)
            envelop.payload = {"type": "chat", "content": response}
            return envelop

    # ----- Execute / Continue / New -----
    doc = result.get("document", {})
    complete = result.get("complete", False)
    next_question = result.get("next_question", "")
    file_mode = result.get("file_mode", "none")
    file_paths = result.get("file_paths", [])

    response = result.get("response", "")
    if any(w in response for w in ["做不到", "搞不定", "无法"]):
        _append_history(session_id, "assistant", response)
        envelop.payload = {"type": "error", "content": response, "stage": "failed"}
        return envelop

    if file_mode == "need_upload":
        _append_history(session_id, "assistant", next_question)
        _save_state(session_id, "collecting", doc, task_id, uploaded_files)
        envelop.payload = {"type": "question", "content": next_question, "stage": "collecting"}
        return envelop

    if complete and doc.get("title"):
        if file_mode == "has_path":
            doc["file_paths"] = file_paths
        if not task_id:
            task_id = f"{int(time.time())}_{random.randint(1000, 9999)}"
        _save_state(session_id, "executing", doc, task_id, uploaded_files)
        _append_history(session_id, "assistant", f"✅ 正在执行：{doc.get('title')}")
        return await _execute_task(envelop, agent, session_id, doc, task_id,
                                   user_input, channel, uploaded_files, email_sender)

    if next_question and channel == "web":
        _append_history(session_id, "assistant", next_question)
        _save_state(session_id, "collecting", doc, task_id, uploaded_files)
        envelop.payload = {"type": "question", "content": next_question, "stage": "collecting"}
        return envelop

    if not complete and channel != "web":
        if not task_id:
            task_id = f"{int(time.time())}_{random.randint(1000, 9999)}"
        doc["_note"] = "部分信息缺失，已使用默认值"
        _save_state(session_id, "executing", doc, task_id, uploaded_files)
        _append_history(session_id, "assistant", f"✅ 正在执行：{doc.get('title')}（部分信息已使用默认值）")
        return await _execute_task(envelop, agent, session_id, doc, task_id,
                                   user_input, channel, uploaded_files, email_sender)

    fallback = "能再详细说说你的需求吗？"
    _append_history(session_id, "assistant", fallback)
    _save_state(session_id, "collecting", {}, task_id, uploaded_files)
    envelop.payload = {"type": "question", "content": fallback, "stage": "collecting"}
    return envelop


# ==================== 执行 ====================
async def _execute_task(envelop, agent, session_id, document, task_id,
                        user_input, channel, uploaded_files, email_sender=""):
    core_inst = get_core()

    task_description = f"""
【用户原始需求】
{user_input}

【结构化描述】
任务: {document.get('title', '未命名')}
描述: {document.get('description', '')}
输入: {document.get('input', '')}
处理: {document.get('process', '')}
输出: {document.get('output', '')}
约束: {document.get('constraints', '无')}
"""

    # 🔥 告诉 dev 文件在哪
    if document.get("file_paths"):
        task_description += f"\n【用户指定文件路径，直接读取，不要从 input_dir 找】\n{json.dumps(document['file_paths'])}"

    if uploaded_files:
        task_description += f"\n【用户上传文件，从 input_dir 读取】"

    async def _on_task_done(result):
        output_files = []
        for fp in result.get("attachments", []):
            p = Path(fp)
            if p.exists():
                try:
                    rel_path = p.relative_to(Path("data"))
                    url_path = f"/data/{rel_path.as_posix()}"
                except ValueError:
                    url_path = str(p)
                output_files.append({
                    "name": p.name,
                    "path": url_path,
                    "size": p.stat().st_size
                })

        if channel == "web":
            from plugins.builtins.notify.ws_notify import push
            await push(agent, f"pa_{session_id}", {
                "type": "done",
                "result": result,
                "files": output_files
            })

        if channel == "email":
            from plugins.builtins.notify.email_notify import send as email_send
            from plugins.builtins.agents.config import get_channel_config
            to_email = email_sender or get_channel_config("email").get("user", "")
            subject = f"✅ 任务完成: {document.get('title', '任务')}"
            body = result.get("body", "")
            attachment_paths = result.get("attachments", [])
            await email_send(agent, to_email, subject, body, attachment_paths)

        _save_state(session_id, "done", document, task_id, [])

    task_id = await core_inst.process_message(
        agent, task_description, session_id, _on_task_done,
        extra={
            "channel": channel,
            "title": document.get("title") or user_input.strip()[:50],
            "ws_channel": session_id,
            "files": uploaded_files
        }
    )

    if uploaded_files:
        core_inst.save_files(task_id, uploaded_files)

    envelop.payload = {
        "type": "task",
        "content": f"✅ 需求已确认，正在执行：{document.get('title', '任务')}",
        "task_id": task_id,
        "document": document
    }
    return envelop


# ==================== 重试 ====================
async def _retry_task(envelop, agent, session_id, task_id, channel):
    core_inst = get_core()

    async def _on_done(result):
        try:
            from plugins.builtins.notify.ws_notify import push
            await push(agent, f"pa_{session_id}", {"type": "done", "result": result})
        except:
            pass

    task_content = core_inst.get_task_content(task_id) or f"[手动执行] {task_id}"
    new_task_id = await core_inst.process_message(
        agent, task_content, session_id, _on_done,
        extra={"channel": channel}
    )

    envelop.payload = {
        "type": "task",
        "content": f"🔄 重新执行任务: {task_id}",
        "task_id": new_task_id
    }
    return envelop


# ==================== 查询接口 ====================
async def list_tasks(envelop, agent):
    envelop.payload = {"ok": True, "data": get_core().list_all_tasks()}
    return envelop


async def delete_task(envelop, agent):
    task_id = envelop.payload.get("task_id", "")
    if not task_id:
        envelop.payload = {"ok": False, "error": "缺少 task_id"}
        return envelop
    ok = get_core().delete_task(task_id)
    envelop.payload = {"ok": ok, "message": "已删除" if ok else "任务不存在"}
    return envelop


async def get_workspace(envelop, agent):
    envelop.payload = {"ok": True, "data": {"items": get_core().get_workspace()}}
    return envelop


async def clear_all_tasks(envelop, agent):
    core_inst = get_core()
    tasks = core_inst.list_all_tasks()
    deleted = 0
    for t in tasks:
        if core_inst.delete_task(t["task_id"]):
            deleted += 1
    envelop.payload = {"ok": True, "deleted": deleted}
    return envelop


async def reset_session(session_id: str):
    _reset_session(session_id)
    return {"ok": True, "session_id": session_id}


__all__ = ["execute", "list_tasks", "delete_task", "get_workspace",
           "clear_all_tasks", "_reset_session", "reset_session"]