# plugins/builtins/agents/scheduler.py
"""定时调度 — 独立时间驱动 + 快照留存"""
import asyncio
import json
import shutil
from pathlib import Path
from datetime import datetime
import core
from plugins.builtins.constants import TASKS_DIR, SCHEDULES_DIR

PROJECT = Path(__file__).parent.name


async def loop(agent):
    await asyncio.sleep(10)
    SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        for d in SCHEDULES_DIR.iterdir():
            if not d.is_dir():
                continue

            config_file = d / "config.json"
            if not config_file.exists():
                continue

            cfg = json.loads(config_file.read_text(encoding="utf-8"))
            if not cfg.get("enabled", True):
                continue
            if not cfg.get("schedule"):
                continue
            if not _should_run(cfg["schedule"], cfg.get("last_run")):
                continue

            task_id = cfg["task_id"]
            task_name = cfg.get("name", task_id)
            task_dir = TASKS_DIR / task_id

            if not task_dir.exists():
                print(f"[scheduler] 任务不存在: {task_id}")
                continue

            print(f"[scheduler] ⏰ 定时执行: {task_name}")

            result = await agent.system.call(core.Envelop(
                sender=f"builtins/agents/scheduler",
                receiver="builtins/agents/os_agent",
                payload={"action": "run_task", "params": {"task_id": task_id}}
            ))

            ok = result.payload.get("ok", False) if result else False

            now = datetime.now()
            snapshot_dir = d / "history" / now.strftime('%Y%m%d_%H%M%S')
            snapshot_dir.mkdir(parents=True, exist_ok=True)

            output_dir = task_dir / "output"
            snap_output = snapshot_dir / "output"
            if output_dir.exists():
                snap_output.mkdir(exist_ok=True)
                for f in output_dir.rglob("*"):
                    if f.is_file():
                        dest = snap_output / f.relative_to(output_dir)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dest)

            (snapshot_dir / "result.json").write_text(
                json.dumps(result.payload if result else {}, ensure_ascii=False, indent=2))
            (snapshot_dir / "meta.json").write_text(json.dumps({
                "task_id": task_id, "task_name": task_name,
                "time": now.isoformat(), "ok": ok
            }, ensure_ascii=False, indent=2))

            attachments = []
            if snap_output.exists():
                for f in snap_output.rglob("*"):
                    if f.is_file():
                        attachments.append(str(f.absolute()))

            notify = cfg.get("notify", {})
            if notify.get("email") and ok:
                try:
                    from plugins.builtins.notify.email_notify import send as email_send
                    body = json.dumps(result.payload.get("result", {}), ensure_ascii=False, indent=2)
                    if attachments:
                        body += f"\n\n📎 产出文件: {len(attachments)} 个"
                        for a in attachments:
                            body += f"\n  - {Path(a).name}"
                    await email_send(agent, notify["email"],
                                     f"⏰ 定时完成: {task_name}", body, attachments)
                except Exception as e:
                    print(f"[scheduler] 通知失败: {e}")

            cfg["last_run"] = now.isoformat()
            cfg["run_count"] = cfg.get("run_count", 0) + 1
            cfg["last_result"] = {
                "time": now.isoformat(), "ok": ok,
                "notified": bool(notify.get("email")),
                "attachments": [Path(a).name for a in attachments]
            }
            config_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

            print(f"[scheduler] ✅ 调度完成: {task_name} (第{cfg['run_count']}次)")

            # 清理旧快照
            history_dir = d / "history"
            if history_dir.exists():
                snapshots = sorted(history_dir.iterdir(), reverse=True)
                for old in snapshots[30:]:
                    try:
                        shutil.rmtree(old)
                    except:
                        pass

        await asyncio.sleep(30)


# ==================== 调度管理 ====================
async def set_schedule(task_id: str, cron: str, notify_email: str = "") -> dict:
    task_dir = TASKS_DIR / task_id
    if not task_dir.exists():
        return {"ok": False, "error": "任务不存在"}

    meta_path = task_dir / "task_meta.json"
    if not meta_path.exists():
        return {"ok": False, "error": "任务元信息不存在"}

    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    schedule_dir = SCHEDULES_DIR / task_id
    schedule_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "task_id": task_id,
        "name": meta.get("title", task_id),
        "enabled": True,
        "schedule": {"every_minutes": _cron_to_minutes(cron)},
        "notify": {"email": notify_email} if notify_email else {},
        "last_run": None,
        "run_count": 0
    }
    (schedule_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2))
    return {"ok": True, "message": "已设置定时"}


async def cancel_schedule(task_id: str) -> dict:
    config_path = SCHEDULES_DIR / task_id / "config.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        cfg["enabled"] = False
        config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        return {"ok": True, "message": "已取消"}
    return {"ok": False, "error": "调度不存在"}


async def delete_schedule(task_id: str) -> dict:
    schedule_dir = SCHEDULES_DIR / task_id
    if schedule_dir.exists():
        shutil.rmtree(schedule_dir)
        return {"ok": True, "message": "调度已删除"}
    return {"ok": False, "error": "调度不存在"}


def get_history(task_id: str) -> list:
    history_dir = SCHEDULES_DIR / task_id / "history"
    if not history_dir.exists():
        return []

    snapshots = []
    for d in sorted(history_dir.iterdir(), reverse=True):
        meta_path = d / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            output_dir = d / "output"
            files = []
            if output_dir.exists():
                for f in output_dir.rglob("*"):
                    if f.is_file():
                        files.append({
                            "name": f.name, "path": str(f.absolute()), "size": f.stat().st_size
                        })
            meta["files"] = files
            snapshots.append(meta)
    return snapshots


async def parse_schedule(envelop, agent):
    text = envelop.payload.get("text", "")
    if not text:
        envelop.payload = {"ok": False, "error": "缺少描述"}
        return envelop

    llm = agent.llm
    raw = await llm.chat([
        {"role": "system", "content": "把自然语言转成 cron 表达式。只输出 cron 字符串。"},
        {"role": "user", "content": text}
    ])
    raw = raw.strip().strip('"').strip("'")
    if raw and len(raw.split()) == 5:
        envelop.payload = {"ok": True, "cron": raw}
    else:
        envelop.payload = {"ok": False, "error": f"解析失败: {raw}"}
    return envelop


# ==================== 工具 ====================
def _should_run(schedule, last_run_str):
    if not schedule:
        return False
    if not last_run_str:
        return True
    now = datetime.now()
    last_run = datetime.fromisoformat(last_run_str)
    every_min = schedule.get("every_minutes", 0)
    if every_min > 0:
        return (now - last_run).total_seconds() >= every_min * 60
    every_sec = schedule.get("every_seconds", 0)
    if every_sec > 0:
        return (now - last_run).total_seconds() >= every_sec
    return False


def _cron_to_minutes(cron: str) -> int:
    parts = cron.strip().split()
    if len(parts) != 5:
        return 60
    minute = parts[0]
    if minute.startswith("*/"):
        return int(minute[2:])
    if minute == "0" and parts[1] == "*":
        return 60
    return 1440


# scheduler.py 新增

def get_schedule_detail(task_id: str) -> dict:
    """获取单个调度详情"""
    config_path = SCHEDULES_DIR / task_id / "config.json"
    if not config_path.exists():
        return {"ok": False, "error": "调度不存在"}
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    
    # 加历史快照数量
    history_dir = SCHEDULES_DIR / task_id / "history"
    cfg["history_count"] = len(list(history_dir.iterdir())) if history_dir.exists() else 0
    
    return {"ok": True, "data": cfg}