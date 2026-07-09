# plugins/builtins/agents/_init.py
"""
主控制台 — 路由门面
统一入口：main_agent
"""
import asyncio
import webbrowser
import core
from pathlib import Path

PROJECT = Path(__file__).parent.name

from plugins.builtins.agents import config
from plugins.builtins.agents import scheduler
from plugins.builtins.agents.main_agent import (
    execute as main_agent_execute,
    list_tasks,
    delete_task,
    get_workspace,
    clear_all_tasks,
)
from plugins.builtins.message_core.core import get_core

# ============================================================
# 通道注册 — 新增通道只需加 import + CHANNEL_ACTIONS
# ============================================================
try:
    from plugins.builtins.channels.email_channel import check_now as email_check, test_send as email_test
    _HAS_EMAIL = True
except ImportError:
    _HAS_EMAIL = False
    email_check = None
    email_test = None

CHANNEL_ACTIONS = {}
if _HAS_EMAIL:
    CHANNEL_ACTIONS.update({
        "check_email": email_check,
        "test_email": email_test,
    })

# 预留扩展
# try:
#     from plugins.builtins.channels.feishu_channel import check_now as feishu_check, test_send as feishu_test
#     CHANNEL_ACTIONS.update({"check_feishu": feishu_check, "test_feishu": feishu_test})
# except ImportError:
#     pass


# ============================================================
# 主路由
# ============================================================

async def execute(envelop, agent):
    payload = envelop.payload.get("payload", envelop.payload)
    action = payload.get("action", "init")

    routes = {
        # 系统
        "init": _init,
        "open_web": _open_web,

        # 配置
        "get_config": config.get,
        "save_config": config.save,

        # 核心入口（统一走 main_agent）
        "process": main_agent_execute,
        "get_session": main_agent_execute,
        "remove_file": main_agent_execute,

        # 任务查询
        "list_tasks": list_tasks,
        "delete_task": delete_task,
        "get_workspace": get_workspace,
        "clear_all_tasks": clear_all_tasks,
        "run_task": _run_task,

        # 调度
        "schedule_task": _schedule_task,
        "cancel_schedule": _cancel_schedule,
        "delete_schedule": _delete_schedule,
        "get_schedule_history": _get_schedule_history,
        "parse_schedule": scheduler.parse_schedule,
        "rerun_task": _rerun_task,
        "get_schedule_detail": _get_schedule_detail,
        "update_schedule": _update_schedule,
    }

    # 通道操作路由注入
    routes.update(CHANNEL_ACTIONS)
    if action in CHANNEL_ACTIONS:
        result = await CHANNEL_ACTIONS[action](agent)
        envelop.payload = result
        return envelop

    handler = routes.get(action)
    if handler:
        return await handler(envelop, agent)

    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop


# ============================================================
# 系统
# ============================================================

async def _init(envelop, agent):
    """启动时初始化：注册菜单 + 启动后台服务"""
    # await agent.system.call(core.Envelop(
    #     sender="builtins/agents/_init",
    #     receiver="shell/_tray",
    #     payload={
    #         "action": "add_menu",
    #         "app_id": "agents",
    #         "label": "主控制台",
    #         "items": [
    #             {
    #                 "label": "打开面板",
    #                 "action": "open_web",
    #                 "url": "/agents/index.html",
    #                 "receiver": "builtins/agents/_init"
    #             },
    #         ],
    #     },
    # ))

    # 启动定时调度循环
    asyncio.create_task(scheduler.loop(agent))

    # 启动邮件渠道
    if _HAS_EMAIL:
        try:
            from plugins.builtins.channels.email_channel import email_loop
            asyncio.create_task(email_loop(agent))
        except ImportError:
            print("[_init] 邮件渠道未加载")

    # 预留：启动飞书渠道
    # try:
    #     from plugins.builtins.channels.feishu_channel import feishu_loop
    #     asyncio.create_task(feishu_loop(agent))
    # except ImportError:
    #     pass

    envelop.payload = {"ok": True}
    return envelop


async def _open_web(envelop, agent):
    webbrowser.open(f"{agent.base_url}/agents/index.html")
    envelop.payload = {"ok": True}
    return envelop


# ============================================================
# 任务执行
# ============================================================

async def _run_task(envelop, agent):
    """手动执行已有任务（复用已有 workflow）"""
    task_id = envelop.payload.get("task_id", "")
    ws_channel = envelop.payload.get("ws_channel", "")

    core_inst = get_core()

    if not core_inst.task_exists(task_id):
        envelop.payload = {"ok": False, "error": "任务不存在"}
        return envelop

    task_content = core_inst.get_task_content(task_id) or f"[手动执行] {task_id}"

    async def _on_done(result):
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

        from plugins.builtins.notify.ws_notify import push
        await push(agent, f"pa_{ws_channel}", {
            "type": "done",
            "result": result,
            "files": output_files
        })

    new_task_id = await core_inst.process_message(
        agent, task_content, ws_channel, _on_done,
        extra={"channel": "web", "ws_channel": ws_channel}
    )

    envelop.payload = {"ok": True, "task_id": new_task_id}
    return envelop

# _init.py 新增

async def _rerun_task(envelop, agent):
    """原地重跑已有任务（不重新生成代码）"""
    import core as core_mod
    
    task_id = envelop.payload.get("task_id", "")
    ws_channel = envelop.payload.get("ws_channel", "")
    
    core_inst = get_core()
    if not core_inst.task_exists(task_id):
        envelop.payload = {"ok": False, "error": "任务不存在"}
        return envelop
    
    if not core_inst.has_workflow(task_id):
        envelop.payload = {"ok": False, "error": "该任务没有 workflow，请用「执行」重新生成"}
        return envelop
    
    # 直接调 os_agent.run_task，不走 dev_agent
    result = await agent.system.call(core_mod.Envelop(
        sender="builtins/agents/_init",
        receiver="builtins/agents/os_agent",
        payload={
            "action": "run_task",
            "params": {"task_id": task_id}
        }
    ))
    
    # 推送结果
    ok = result.payload.get("ok", False) if result else False
    from plugins.builtins.notify.ws_notify import push
    await push(agent, f"pa_{ws_channel}", {
        "type": "done",
        "result": result.payload if result else {},
        "files": []
    })
    
    envelop.payload = {"ok": ok, "task_id": task_id}
    return envelop
# ============================================================
# 调度
# ============================================================

async def _schedule_task(envelop, agent):
    task_id = envelop.payload.get("task_id", "")
    cron = envelop.payload.get("cron", "0 8 * * *")
    notify_email = envelop.payload.get("notify_email", "")
    result = await scheduler.set_schedule(task_id, cron, notify_email)
    envelop.payload = result
    return envelop


async def _cancel_schedule(envelop, agent):
    task_id = envelop.payload.get("task_id", "")
    result = await scheduler.cancel_schedule(task_id)
    envelop.payload = result
    return envelop


async def _delete_schedule(envelop, agent):
    task_id = envelop.payload.get("task_id", "")
    result = await scheduler.delete_schedule(task_id)
    envelop.payload = result
    return envelop


async def _get_schedule_history(envelop, agent):
    task_id = envelop.payload.get("task_id", "")
    history = scheduler.get_history(task_id)
    envelop.payload = {"ok": True, "history": history}
    return envelop


async def _get_schedule_detail(envelop, agent):
    task_id = envelop.payload.get("task_id", "")
    result = scheduler.get_schedule_detail(task_id)
    envelop.payload = result
    return envelop

async def _update_schedule(envelop, agent):
    task_id = envelop.payload.get("task_id", "")
    cron = envelop.payload.get("cron", "")
    notify_email = envelop.payload.get("notify_email", "")
    result = await scheduler.set_schedule(task_id, cron, notify_email)
    envelop.payload = result
    return envelop