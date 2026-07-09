# plugins/builtins/message_core/core.py
"""通用消息处理内核 — 唯一任务生命周期管理"""
import asyncio
import json
import uuid
import zipfile
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, Callable, Any, Optional
import core
from plugins.builtins.constants import TASKS_DIR


class MessageCore:
    
    def __init__(self, name: str = "default"):
        self.name = name
        self.tasks_dir = TASKS_DIR
        self._callbacks: Dict[str, Callable] = {}
        self._result_store: Dict[str, Any] = {}
        self._task_status: Dict[str, dict] = {}
        self._task_meta: Dict[str, dict] = {}
        self._task_retry: Dict[str, int] = {}
        self.MAX_RETRIES = 2
        self.TASK_TIMEOUT = 600
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
    
    # ==================== 任务创建 ====================
    def create_task(self, content: str, sender: str = "unknown",
                    extra: dict = None) -> str:
        task_id = f"{self.name}_{uuid.uuid4().hex[:8]}"
        work_dir = self.tasks_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "input").mkdir(exist_ok=True)
        (work_dir / "output").mkdir(exist_ok=True)
        
        extra = extra or {}
        now = datetime.now()
        channel = extra.get("channel", "web")
        title = extra.get("title") or content[:50]
        
        meta = {
            "task_id": task_id, "title": title, "channel": channel,
            "sender": sender, "content": content[:500], "extra": extra,
            "created_at": now.isoformat()
        }
        (work_dir / "task_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        
        self._task_meta[task_id] = meta
        self._task_status[task_id] = {"status": "created", "created_at": now, "callback_done": False}
        return task_id
    
    def get_work_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id
    
    def get_input_dir(self, task_id: str) -> Path:
        return self.get_work_dir(task_id) / "input"
    
    def get_output_dir(self, task_id: str) -> Path:
        return self.get_work_dir(task_id) / "output"
    
    # ==================== 防失控 ====================
    def _should_abort(self, task_id: str) -> Optional[str]:
        self._task_retry[task_id] = self._task_retry.get(task_id, 0) + 1
        if self._task_retry[task_id] > self.MAX_RETRIES:
            return f"超过最大重试次数 ({self.MAX_RETRIES})"
        status = self._task_status.get(task_id, {})
        created = status.get("created_at")
        if created:
            elapsed = (datetime.now() - created).total_seconds()
            if elapsed > self.TASK_TIMEOUT:
                return f"任务超时 ({int(elapsed)}s)"
        return None
    
    def _abort_task(self, task_id: str, reason: str) -> dict:
        self._task_status[task_id] = {"status": "aborted", "reason": reason, "callback_done": False}
        return {
            "task_id": task_id, "ok": False,
            "body": f"任务已中止: {reason}", "data": {}, "attachments": [],
            "error": reason, "aborted": True
        }
    
    # ==================== 回调 ====================
    def on_complete(self, task_id: str, callback: Callable):
        self._callbacks[task_id] = callback
    
    async def trigger_callback(self, task_id: str, result: dict):
        status = self._task_status.get(task_id, {})
        if status.get("callback_done"):
            return
        callback = self._callbacks.pop(task_id, None)
        if callback:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(result)
                else:
                    ret = callback(result)
                    if asyncio.iscoroutine(ret):
                        await ret
            except Exception as e:
                print(f"[MessageCore] 回调失败: {e}")
        self._task_status[task_id]["callback_done"] = True
    
    def store_result(self, task_id: str, result: dict):
        self._result_store[task_id] = {"result": result, "time": datetime.now().isoformat()}
    
    def get_result(self, task_id: str) -> Optional[dict]:
        return self._result_store.pop(task_id, None)
    
    # ==================== 文件处理 ====================
    def save_files(self, task_id: str, files: list):
        import base64
        input_dir = self.get_input_dir(task_id)
        input_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            file_name = f.get("name", "unnamed")
            file_content = f.get("content", "")
            if file_content:
                (input_dir / file_name).write_bytes(base64.b64decode(file_content))
    
    # ==================== 输出收集 ====================
    def collect_output(self, task_id: str) -> list:
        output_dir = self.get_output_dir(task_id)
        attachments = []
        if output_dir.exists():
            all_files = [f for f in output_dir.rglob("*") if f.is_file()]
            if len(all_files) > 5:
                zip_path = output_dir / f"{task_id}_output.zip"
                self._create_zip(output_dir, zip_path, all_files)
                attachments = [str(zip_path)]
            elif all_files:
                attachments = [str(f) for f in all_files]
        return attachments
    
    def read_result(self, task_id: str) -> str:
        result_file = self.get_output_dir(task_id) / 'result.txt'
        if result_file.exists():
            return result_file.read_text(encoding='utf-8', errors='ignore')
        return ""
    
    def save_record(self, task_id: str, result: dict):
        record = {
            "task_id": task_id, "time": datetime.now().isoformat(),
            "ok": result.get("ok", False), "body": result.get("body", "")[:500],
            "attachments": [Path(a).name for a in result.get("attachments", [])],
            "error": result.get("error"), "aborted": result.get("aborted", False)
        }
        (self.get_work_dir(task_id) / "task_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2))
    
    def _create_zip(self, source_dir: Path, output_path: Path, files: list):
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in files:
                if file != output_path:
                    zf.write(file, file.relative_to(source_dir))
    
    # ==================== 完整流程 ====================
    async def process_message(self, agent, content: str, sender: str,
                              reply_func: Callable, extra: dict = None) -> str:
        task_id = self.create_task(content, sender, extra)
        self.on_complete(task_id, reply_func)
        asyncio.create_task(self._execute_task(agent, task_id, content, sender))
        return task_id
    
    async def process_message_sync(self, agent, content: str, sender: str,
                                   extra: dict = None) -> dict:
        task_id = self.create_task(content, sender, extra)
        return await self._execute_task(agent, task_id, content, sender)
    
    async def _execute_task(self, agent, task_id: str, task_desc: str,
                            sender: str) -> dict:
        abort_reason = self._should_abort(task_id)
        if abort_reason:
            error_result = self._abort_task(task_id, abort_reason)
            await self.trigger_callback(task_id, error_result)
            return error_result
        
        self._task_status[task_id]["status"] = "processing"
        
        try:
            result = await self._call_os_agent(agent, task_id, task_desc, sender)
            attachments = self.collect_output(task_id)
            result_body = self.read_result(task_id)
            ok = result and result.payload.get("ok") if result else False
            
            task_result = {
                "task_id": task_id, "ok": ok,
                "body": result_body or ("任务执行完成" if ok else "任务执行失败"),
                "data": result.payload.get("result", {}) if result else {},
                "attachments": attachments,
                "error": result.payload.get("error") if result and not ok else None,
                "aborted": False
            }
            
            self.save_record(task_id, task_result)
            self._task_status[task_id]["status"] = "done"
            await self.trigger_callback(task_id, task_result)
            return task_result
            
        except Exception as e:
            print(f"[MessageCore] 异常: {e}")
            traceback.print_exc()
            error_result = {
                "task_id": task_id, "ok": False,
                "body": f"系统异常: {str(e)}", "data": {}, "attachments": [],
                "error": str(e), "aborted": True
            }
            self._task_status[task_id]["status"] = "error"
            await self.trigger_callback(task_id, error_result)
            return error_result
    
    async def _call_os_agent(self, agent, task_id: str, task_desc: str,
                             sender: str) -> Any:
        work_dir = self.get_work_dir(task_id)
        enhanced_task = f"""
[任务ID: {task_id}]
[来源: {sender}]

{task_desc}

目录说明:
- 输入目录: {work_dir / 'input'} （用户附件/文件在这里）
- 输出目录: {work_dir / 'output'} （默认输出位置）
- 工作目录: {work_dir}

要求:
1. 用户如果指定了输出路径，必须保存到用户指定路径，同时复制一份到输出目录
2. 用户如果指定了输入路径，优先从用户指定路径读取
3. 如果 input/ 有文件，处理这些文件
4. 输出目录作为兜底：用户没指定输出路径时才只用输出目录
5. 执行结果写入 {work_dir / 'output' / 'result.txt'}
"""
        return await agent.system.call(core.Envelop(
            sender=f"message_core/{self.name}",
            receiver="builtins/agents/os_agent",
            payload={
                "action": "run",
                "params": {"task": enhanced_task, "work_dir": str(work_dir), "task_id": task_id}
            }
        ))
    
    # ==================== 查询接口 ====================
    def list_all_tasks(self) -> list:
        from plugins.builtins.constants import SCHEDULES_DIR
        
        tasks = []
        if not self.tasks_dir.exists():
            return tasks
        for task_dir in sorted(self.tasks_dir.iterdir(), reverse=True):
            if not task_dir.is_dir():
                continue
            meta_path = task_dir / "task_meta.json"
            record_path = task_dir / "task_record.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            task_info = {
                "task_id": meta.get("task_id"),
                "title": meta.get("title", ""),
                "channel": meta.get("channel", "web"),
                "sender": meta.get("sender", ""),
                "created_at": meta.get("created_at", ""),
                "has_workflow": (task_dir / "workflow.json").exists(),
            }
            
            if record_path.exists():
                record = json.loads(record_path.read_text(encoding='utf-8'))
                task_info["ok"] = record.get("ok")
                task_info["aborted"] = record.get("aborted")
            
            # 调度信息
            schedule_path = SCHEDULES_DIR / task_dir.name / "config.json"
            if schedule_path.exists():
                task_info["has_schedule"] = True
                try:
                    sched = json.loads(schedule_path.read_text(encoding='utf-8'))
                    task_info["schedule"] = {
                        "cron": sched.get("cron") or f"每{sched.get('schedule', {}).get('every_minutes', '?')}分钟",
                        "enabled": sched.get("enabled", True),
                        "run_count": sched.get("run_count", 0),
                        "last_run": sched.get("last_run"),
                    }
                except:
                    task_info["has_schedule"] = True
                    task_info["schedule"] = {"cron": "—"}
            else:
                task_info["has_schedule"] = False
            
            # 产出文件
            output_dir = task_dir / "output"
            if output_dir.exists():
                files = []
                for f in output_dir.rglob("*"):
                    if f.is_file():
                        rel = str(f.relative_to(Path("."))).replace("\\", "/")
                        files.append({"name": f.name, "path": "/" + rel, "size": f.stat().st_size})
                task_info["output_files"] = files
            
            tasks.append(task_info)
        return tasks
    
    def get_workspace(self) -> list:
        items = []
        for task_dir in sorted(self.tasks_dir.iterdir(), reverse=True):
            if not task_dir.is_dir():
                continue
            meta_path = task_dir / "task_meta.json"
            latest_path = task_dir / "task_record.json"
            meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
            if latest_path.exists():
                record = json.loads(latest_path.read_text(encoding='utf-8'))
                output_dir = task_dir / "output"
                output_files = []
                if output_dir.exists():
                    for f in output_dir.rglob("*"):
                        if f.is_file():
                            output_files.append({
                                "name": f.name,
                                "path": str(f.relative_to(Path("."))).replace("\\", "/"),
                                "size": f.stat().st_size
                            })
                items.append({
                    "task_id": task_dir.name, "task_name": meta.get("title", task_dir.name),
                    "task_desc": meta.get("content", ""), "time": record.get("time", ""),
                    "ok": record.get("ok", False), "output_files": output_files
                })
        return items
    
    def delete_task(self, task_id: str) -> bool:
        import shutil
        work_dir = self.get_work_dir(task_id)
        if work_dir.exists():
            shutil.rmtree(work_dir)
            self._task_status.pop(task_id, None)
            self._task_meta.pop(task_id, None)
            self._callbacks.pop(task_id, None)
            self._result_store.pop(task_id, None)
            return True
        return False
    
    def task_exists(self, task_id: str) -> bool:
        return self.get_work_dir(task_id).exists()
    
    def has_workflow(self, task_id: str) -> bool:
        return (self.get_work_dir(task_id) / "workflow.json").exists()
    
    def get_task_content(self, task_id: str) -> str:
        meta_path = self.get_work_dir(task_id) / "task_meta.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding='utf-8')).get("content", "")
        return ""


_core = MessageCore("agents")


def get_core() -> MessageCore:
    return _core