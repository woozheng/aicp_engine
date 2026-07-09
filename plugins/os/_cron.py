"""定时任务插件 — 协议 v3.0 系统插件
按间隔触发指定插件。
"""
import asyncio
from typing import Dict
import aiohttp
import core


_tasks: Dict[str, asyncio.Task] = {}


async def execute(envelop, agent):
    action = envelop.payload.get("action", "")
    
    if action == "schedule":
        task_id = envelop.payload.get("task_id", "")
        interval = envelop.payload.get("interval", 60)
        target_receiver = envelop.payload.get("target_receiver", "")
        target_payload = envelop.payload.get("target_payload", {})
        
        if not task_id or not target_receiver:
            envelop.payload = {"error": "task_id and target_receiver required"}
            return envelop
        
        if task_id in _tasks:
            envelop.payload = {"error": f"Task {task_id} already exists"}
            return envelop
        
        base_url = agent.base_url
        
        async def cron_loop():
            while task_id in _tasks:
                await asyncio.sleep(interval)
                if task_id not in _tasks:
                    break
                try:
                    async with aiohttp.ClientSession() as session:
                        await session.post(
                            f"{base_url}/api/{target_receiver}",
                            json={"payload": target_payload},
                        )
                except Exception as e:
                    agent.log.error(f"Cron {task_id}: {e}")
        
        _tasks[task_id] = asyncio.get_event_loop().create_task(cron_loop())
        envelop.payload = {"ok": True, "task_id": task_id, "interval": interval}
        return envelop
    
    elif action == "cancel":
        task_id = envelop.payload.get("task_id", "")
        
        if task_id not in _tasks:
            envelop.payload = {"error": f"Task {task_id} not found"}
            return envelop
        
        _tasks[task_id].cancel()
        del _tasks[task_id]
        envelop.payload = {"ok": True, "task_id": task_id}
        return envelop
    
    elif action == "list":
        tasks_info = {}
        for tid, task in _tasks.items():
            tasks_info[tid] = {
                "running": not task.done(),
                "cancelled": task.cancelled(),
            }
        envelop.payload = {"tasks": tasks_info}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop