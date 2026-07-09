"""
OS Agent — 调度执行。收需求 → 调 dev_agent 生成 → 执行 workflow
自动安装缺失依赖 + 增量执行 + 错误重试 + 契约校验 + 自动修复
"""
import core
import json
import traceback
import importlib.util
import importlib
import subprocess
import asyncio
import time
import re
import copy
from pathlib import Path
from datetime import datetime

_PKG_ALIAS = {
    "pptx": "python-pptx",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "fpdf": "fpdf2",
    "docx": "python-docx",
    "xlsxwriter": "xlsxwriter",
    "xlrd": "xlrd",
    "openpyxl": "openpyxl",
    "plotly": "plotly",
    "matplotlib": "matplotlib",
}

# 🔥 新增：导入名 → 实际安装包名 映射
_INSTALL_ALIAS = {
    "pdf2docx": "pdf2docx-plus",
}

TASKS_DIR = Path("data/agents/tasks")


import threading

_installed_packages = set()
_install_lock = threading.Lock()
_installing = set()  # 正在安装中的包，防止重复安装


def help():
    return {
        "route": "/api/builtins/agents/os_agent",
        "actions": {
            "run": {"params": {"task": "需求", "work_dir": "工作目录"}},
            "run_task": {"params": {"task_id": "任务ID"}},
            "retry": {"params": {"task_id": "任务ID"}},
            "validate": {"params": {"task_id": "任务ID"}},
        }
    }


# ============================================================
# 🔥 统一结果判断函数
# ============================================================
def _is_success(result_data) -> bool:
    """判断插件返回结果是否成功，兼容多种返回格式"""
    if result_data is None:
        return False
    if not isinstance(result_data, dict):
        return False
    
    # 优先检查 ok 字段
    if "ok" in result_data:
        return result_data.get("ok") is True
    # 兼容 success 字段
    if "success" in result_data:
        return result_data.get("success") is True
    # 如果有 error 字段，说明失败
    if "error" in result_data:
        return False
    # 如果有 error 字段（大写）
    if "Error" in result_data:
        return False
    # 默认：如果有 data 或 result，认为成功
    if "data" in result_data or "result" in result_data:
        return True
    return False


def _get_error_message(result_data) -> str:
    if result_data is None:
        return "无返回结果"
    if not isinstance(result_data, dict):
        return str(result_data)
    if "error" in result_data:
        return str(result_data["error"])
    if "Error" in result_data:
        return str(result_data["Error"])
    # 🔥 检查 data 里是否有 error
    if isinstance(result_data.get("data"), dict) and "error" in result_data["data"]:
        return str(result_data["data"]["error"])
    if "message" in result_data and not _is_success(result_data):
        return str(result_data["message"])
    return "未知错误"


async def execute(envelop, agent):
    action = envelop.payload.get("action", "run")
    params = envelop.payload.get("params", {})

    if action == "run":
        return await _run(envelop, agent, params)
    elif action == "run_task":
        return await _run_task(envelop, agent, params)
    elif action == "retry":
        return await _retry_task(envelop, agent, params)
    elif action == "validate":
        return await _validate_task(envelop, agent, params)

    envelop.payload = {"ok": False, "error": f"不支持的操作: {action}"}
    return envelop


async def _push(agent, task_id, msg_type, data):
    try:
        await asyncio.wait_for(
            agent.system.call(core.Envelop(
                sender="builtins/agents/os_agent",
                receiver="os/_websocket",
                payload={
                    "action": "push",
                    "channel_id": f"pa_{task_id}",
                    "data": {"type": msg_type, "task_id": task_id, **data}
                }
            )),
            timeout=5
        )
    except (asyncio.TimeoutError, Exception):
        pass


def _ensure_package(pkg_name: str) -> bool:
    """线程安全地确保包已安装"""
    install_name = _INSTALL_ALIAS.get(pkg_name, pkg_name)
    install_name = _PKG_ALIAS.get(install_name, install_name)
    
    # 🔥 快速路径：已安装，无锁检查
    if install_name in _installed_packages:
        return True

    # 🔥 使用锁保护安装过程
    with _install_lock:
        # 双重检查：获取锁后再次确认
        if install_name in _installed_packages:
            return True
        
        # 检查是否正在安装（防止多个线程同时安装同一个包）
        if install_name in _installing:
            print(f"[os_agent] ⏳ {install_name} 正在安装中，等待...")
            # 等待安装完成（最多等60秒）
            for _ in range(60):
                if install_name in _installed_packages:
                    return True
                time.sleep(1)
            print(f"[os_agent] ⚠️ {install_name} 安装等待超时")
            return False
        
        _installing.add(install_name)
    
    # 🔥 锁外执行实际的导入和安装（避免长时间持锁）
    try:
        # 先尝试导入
        try:
            importlib.import_module(pkg_name)
            with _install_lock:
                _installed_packages.add(install_name)
            return True
        except ImportError:
            pass

        # 尝试备选导入名
        if install_name != pkg_name:
            try:
                importlib.import_module(install_name.replace("-", "_"))
                with _install_lock:
                    _installed_packages.add(install_name)
                return True
            except ImportError:
                pass

        # 执行安装
        print(f"[os_agent] 📦 自动安装: {install_name}")
        try:
            import sys
            subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "--ignore-installed",
                 install_name,
                 "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                check=True, capture_output=True, timeout=360
            )
            with _install_lock:
                _installed_packages.add(install_name)
            print(f"[os_agent] ✅ {install_name} 安装成功")
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"[os_agent] ❌ {install_name} 安装失败")
            print(f"[os_agent] stdout: {e.stdout.decode() if e.stdout else '无'}")
            print(f"[os_agent] stderr: {e.stderr.decode() if e.stderr else '无'}")
            
            # 尝试替代包名
            if "bs4" in str(e) or "No matching" in str(e):
                fallback_map = {"bs4": "beautifulsoup4"}
                real_name = fallback_map.get(pkg_name, pkg_name)
                if real_name != pkg_name:
                    print(f"[os_agent] 🔄 尝试替代包名: {real_name}")
                    return _ensure_package(real_name)
            return False
            
        except Exception as e:
            print(f"[os_agent] ❌ {install_name} 安装异常: {e}")
            return False
            
    finally:
        # 🔥 确保清理安装状态
        with _install_lock:
            _installing.discard(install_name)


# ============================================================
# 校验和重试
# ============================================================

async def _validate_task(envelop, agent, params):
    task_id = params.get("task_id", "")
    work_dir = TASKS_DIR / task_id

    if not work_dir.exists():
        envelop.payload = {"ok": False, "error": f"任务不存在: {task_id}"}
        return envelop

    workflow_path = work_dir / "workflow.json"
    if not workflow_path.exists():
        envelop.payload = {"ok": False, "error": "工作流文件不存在"}
        return envelop

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        envelop.payload = {"ok": False, "error": f"工作流解析失败: {e}"}
        return envelop

    is_valid, errors = _validate_chain(workflow)
    envelop.payload = {
        "ok": is_valid,
        "valid": is_valid,
        "errors": errors,
        "workflow": workflow
    }
    return envelop


async def _retry_task(envelop, agent, params):
    task_id = params.get("task_id", "")
    work_dir = TASKS_DIR / task_id

    if not work_dir.exists():
        envelop.payload = {"ok": False, "error": f"任务不存在: {task_id}"}
        return envelop

    workflow_path = work_dir / "workflow.json"
    if not workflow_path.exists():
        envelop.payload = {"ok": False, "error": "工作流文件不存在"}
        return envelop

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        envelop.payload = {"ok": False, "error": f"工作流解析失败: {e}"}
        return envelop

    latest_result_path = work_dir / "latest_result.json"
    if latest_result_path.exists():
        try:
            latest = json.loads(latest_result_path.read_text(encoding="utf-8"))
            last_success = latest.get("result", {}).get("last_success", -1)
            steps = workflow.get("steps", [])
            steps = steps[last_success + 1:] if last_success >= 0 else steps
            workflow["steps"] = steps
        except:
            pass

    output_dir = work_dir / "output"
    input_dir = work_dir / "input"
    for step in workflow.get("steps", []):
        if "plugin" in step and "receiver" not in step:
            step["receiver"] = f"tasks/{task_id}/plugins/{step['plugin']}"
        if "payload" not in step:
            step["payload"] = {"action": "default", "params": {}}
        if "params" not in step["payload"]:
            step["payload"]["params"] = {}
        step["payload"]["params"]["output_dir"] = str(output_dir)
        step["payload"]["params"]["input_dir"] = str(input_dir)

    try:
        final_result = await asyncio.wait_for(
            _execute_workflow(agent, workflow, work_dir, task_id),
            timeout=300
        )
    except asyncio.TimeoutError:
        final_result = {"ok": False, "error": "工作流执行超时"}

    ok = _is_success(final_result) if final_result else False
    _write_result(work_dir, task_id, ok, final_result)

    envelop.payload = {"ok": True, "result": final_result}
    return envelop


def _validate_chain(workflow):
    steps = workflow.get("steps", [])
    prev_outputs = {}
    errors = []

    for i, step in enumerate(steps):
        plugin_name = step.get("plugin", Path(step.get("receiver", "")).stem)
        input_fields = step.get("input", {})
        output_fields = step.get("output", {})

        for key in input_fields.keys():
            if key not in prev_outputs:
                errors.append(f"步骤 {i+1} ({plugin_name}) 需要输入字段 '{key}'，但上一步没提供")
        
        if output_fields:
            prev_outputs.update({k: "string" for k in output_fields.keys()})
        else:
            prev_outputs["data"] = "dict"

    return len(errors) == 0, errors


# ============================================================
# 🔥 核心：通知 Dev Agent 修复
# ============================================================

async def _notify_dev_to_fix(agent, work_dir, task_id, plugin_name, error_msg, step_payload, 
                              workflow_steps, current_step_idx, cached_results=None):
    """通知 Dev Agent 修复失败的插件，带上完整上下文"""
    
    print(f"[os_agent] 🔧 通知 Dev Agent 修复: {plugin_name}")
    
    plugin_path = work_dir / "plugins" / f"{plugin_name}.py"
    code = ""
    if plugin_path.exists():
        code = plugin_path.read_text(encoding="utf-8")
    
    workflow_path = work_dir / "workflow.json"
    workflow = {}
    if workflow_path.exists():
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    
    # 已成功步骤的上下文
    success_context = ""
    if cached_results:
        success_context = f"""
已成功执行的步骤（不需要重新生成）：
{json.dumps(cached_results, ensure_ascii=False, indent=2)}
"""
    
    # 🔥 读取上游插件的实际输出，帮助 LLM 对齐字段名
    upstream_output = ""
    if cached_results and current_step_idx > 0:
        prev_step_name = workflow_steps[current_step_idx - 1].get("receiver", "").split("/")[-1]
        if prev_step_name in cached_results:
            prev_data = cached_results[prev_step_name].get("data", {})
            upstream_output = f"""
上游插件「{prev_step_name}」的实际输出字段：
{json.dumps(list(prev_data.keys()), ensure_ascii=False)}

你必须用 prev.data.get("字段名") 读取这些字段，禁止自己编造字段名。
"""
    
    fix_task = f"""
⚠️ 修复插件 {plugin_name}.py，这个插件执行失败了。

【错误信息】
{error_msg}

【当前插件代码】
{code}

【完整 workflow 参考】
{json.dumps(workflow, ensure_ascii=False, indent=2)[:1500]}

{upstream_output}

{success_context}

【修复要求】
1. 只修复导致错误的代码，不要改变功能，不要简化分析逻辑
2. 确保所有 import 正确
3. 如果错误是"加载失败"或"未知错误"，检查代码完整性和语法
4. 如果错误是"缺少字段"，检查上游实际输出的字段名，不要自己编造字段名
5. 只输出 === PLUGIN: {plugin_name} === 块，末尾必须有 === END ===
6. 不要输出 STRUCTURE 和 WORKFLOW
7. 不要为了修 bug 而删除业务逻辑
"""
    
    result = await agent.system.call(core.Envelop(
        sender="builtins/agents/os_agent",
        receiver="builtins/agents/dev_agent",
        payload={
            "action": "build",
            "params": {
                "task": fix_task,
                "work_dir": str(work_dir),
                "force_single_plugin": True,     # 只修一个插件，不要重新设计多插件
                "force_plugin_name": plugin_name  # 指定修复的插件名
            }
        }
    ))
    
    # 🔥 修复成功后记录失败模式
    if result and _is_success(result.payload):
        print(f"[os_agent] ✅ Dev Agent 已修复: {plugin_name}")
        fingerprint = _extract_failure_fingerprint(error_msg)
        if fingerprint:
            _record_failure_pattern(fingerprint, plugin_name, True)
        return True
    else:
        error = _get_error_message(result.payload) if result else "无响应"
        print(f"[os_agent] ❌ Dev Agent 修复失败: {error}")
        fingerprint = _extract_failure_fingerprint(error_msg)
        if fingerprint:
            _record_failure_pattern(fingerprint, plugin_name, False)
        return False
# ============================================================
# 主流程
# ============================================================

async def _run(envelop, agent, params):
    task = params.get("task", "")
    work_dir = Path(params.get("work_dir", ""))

    if not task or not work_dir:
        envelop.payload = {"ok": False, "error": "缺少 task 或 work_dir"}
        return envelop

    task_id = work_dir.name
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "input").mkdir(exist_ok=True)
    (work_dir / "output").mkdir(exist_ok=True)

    # ============================================================
    # 🔥 DevAgent 重试（最多 3 次）
    # ============================================================
    max_retries = 3
    result = None

    for attempt in range(max_retries):
        result = await agent.system.call(core.Envelop(
            sender="builtins/agents/os_agent",
            receiver="builtins/agents/dev_agent",
            payload={
                "action": "build",
                "params": {"task": task, "work_dir": str(work_dir)}
            }
        ))

        if result and _is_success(result.payload):
            print(f"[os_agent] ✅ DevAgent 生成成功 (尝试 {attempt+1})")
            break

        print(f"[os_agent] 🔄 DevAgent 失败，重试 {attempt+1}/{max_retries}")
        await asyncio.sleep(2)

    # 如果全部失败
    if not result or not _is_success(result.payload):
        error_msg = _get_error_message(result.payload) if result else "dev_agent 无响应"
        _write_result(work_dir, task_id, False, error_msg)
        envelop.payload = {"ok": False, "error": error_msg}
        return envelop

    # ============================================================
    # 后续 workflow 处理
    # ============================================================
    workflow_path = work_dir / "workflow.json"
    if not workflow_path.exists():
        plugin_files = list((work_dir / "plugins").glob("*.py"))
        if not plugin_files:
            _write_result(work_dir, task_id, False, "未生成任何插件")
            envelop.payload = {"ok": False, "error": "未生成任何插件"}
            return envelop
        
        structure_path = work_dir / "structure.json"
        if structure_path.exists():
            structure = json.loads(structure_path.read_text(encoding="utf-8"))
            plugin_order = structure.get("plugins", [])
            ordered_files = []
            for name in plugin_order:
                pf = work_dir / "plugins" / Path(name).name
                if pf.exists():
                    ordered_files.append(pf)
            plugin_files = ordered_files if ordered_files else sorted(plugin_files)
        else:
            plugin_files = sorted(plugin_files)
        
        steps = []
        for pf in plugin_files:
            steps.append({
                "receiver": f"tasks/{task_id}/plugins/{pf.stem}",
                "payload": {"action": "execute", "params": {}}
            })
        default_workflow = {"steps": steps}
        workflow_path.write_text(json.dumps(default_workflow, ensure_ascii=False, indent=2))
        print(f"[os_agent] ✅ 自动生成 workflow: {workflow_path}")

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        print(f"[os_agent] 📄 workflow 内容: {json.dumps(workflow, ensure_ascii=False, indent=2)[:500]}")
    
        workflow = _normalize_workflow(workflow, task_id)
        workflow = _ensure_workflow(work_dir, task_id, workflow)
        workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
        print(f"[os_agent] ✅ workflow 已规范化")
        
    except json.JSONDecodeError as e:
        _write_result(work_dir, task_id, False, f"工作流解析失败: {e}")
        envelop.payload = {"ok": False, "error": f"工作流解析失败: {e}"}
        return envelop

    is_valid, errors = _validate_chain(workflow)
    if not is_valid:
        _write_result(work_dir, task_id, False, f"数据链校验失败: {errors}")
        envelop.payload = {"ok": False, "error": f"数据链校验失败: {errors}"}
        return envelop

    output_dir = work_dir / "output"
    input_dir = work_dir / "input"
    for step in workflow.get("steps", []):
        if "plugin" in step and "receiver" not in step:
            step["receiver"] = f"tasks/{task_id}/plugins/{step['plugin']}"
        if "payload" not in step:
            step["payload"] = {"action": step.get("action", "execute"), "params": step.get("params", {})}
        if "params" not in step["payload"]:
            step["payload"]["params"] = {}
        step["payload"]["params"]["output_dir"] = str(output_dir)
        step["payload"]["params"]["input_dir"] = str(input_dir)

    print(f"[os_agent] 🚀 开始执行工作流，共 {len(workflow.get('steps', []))} 步")

    final_result = await _execute_workflow(agent, workflow, work_dir, task_id)

    now = datetime.now()
    result_dir = work_dir / "results"
    result_dir.mkdir(exist_ok=True)
    log_entry = {
        "task_id": task_id,
        "task": task,
        "time": now.isoformat(),
        "result": final_result,
        "last_success": final_result.get("last_success", -1) if final_result else -1
    }
    (result_dir / f"{now.strftime('%Y%m%d_%H%M%S')}.json").write_text(
        json.dumps(log_entry, ensure_ascii=False, indent=2))
    (work_dir / "latest_result.json").write_text(
        json.dumps(log_entry, ensure_ascii=False, indent=2))

    ok = _is_success(final_result) if final_result else False
    _write_result(work_dir, task_id, ok, final_result)

    envelop.payload = {"ok": True, "result": final_result}

    schedule_path = work_dir / "schedule.json"
    if schedule_path.exists():
        try:
            from plugins.builtins.agents import scheduler
            schedule_config = json.loads(schedule_path.read_text(encoding='utf-8'))
            cron = schedule_config.get("cron", "0 8 * * *")
            notify_email = schedule_config.get("notify", {}).get("email", "")
            await scheduler.set_schedule(task_id, cron, notify_email)
            agent.log.info(f"[os_agent] ⏰ 自动注册调度: {task_id}")
        except Exception as e:
            agent.log.warning(f"[os_agent] 调度注册失败: {e}")
    return envelop


async def _run_task(envelop, agent, params):
    task_id = params.get("task_id", "")
    test_mode = params.get("test_mode", False)  # 🔥 新增：接收测试模式标志
    work_dir = TASKS_DIR / task_id

    if not work_dir.exists():
        envelop.payload = {"ok": False, "error": f"任务不存在: {task_id}"}
        return envelop

    workflow_path = work_dir / "workflow.json"
    if not workflow_path.exists():
        envelop.payload = {"ok": False, "error": "工作流文件不存在"}
        return envelop

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        if test_mode:
            print(f"[os_agent] 🧪 测试模式执行: {task_id}，共 {len(workflow.get('steps', []))} 步")
        else:
            print(f"[os_agent] 📄 workflow 内容: {json.dumps(workflow, ensure_ascii=False, indent=2)[:500]}")
   
        workflow = _normalize_workflow(workflow, task_id)
        workflow = _ensure_workflow(work_dir, task_id, workflow)
        workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
    except json.JSONDecodeError as e:
        envelop.payload = {"ok": False, "error": f"工作流解析失败: {e}"}
        return envelop

    is_valid, errors = _validate_chain(workflow)
    if not is_valid:
        envelop.payload = {"ok": False, "error": f"数据链校验失败: {errors}"}
        return envelop

    output_dir = work_dir / "output"
    input_dir = work_dir / "input"
    for step in workflow.get("steps", []):
        if "plugin" in step and "receiver" not in step:
            step["receiver"] = f"tasks/{task_id}/plugins/{step['plugin']}"
        if "payload" not in step:
            step["payload"] = {"action": step.get("action", "execute"), "params": step.get("params", {})}
        if "params" not in step["payload"]:
            step["payload"]["params"] = {}
        step["payload"]["params"]["output_dir"] = str(output_dir)
        step["payload"]["params"]["input_dir"] = str(input_dir)

    print(f"[os_agent] 🚀 开始执行工作流，共 {len(workflow.get('steps', []))} 步")

    # 🔥 测试模式：降低超时时间，快速反馈
    timeout = 120 if test_mode else 300
    try:
        final_result = await asyncio.wait_for(
            _execute_workflow(agent, workflow, work_dir, task_id, test_mode=test_mode),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        final_result = {"ok": False, "error": "工作流执行超时"}

    # 🔥 测试模式：不写日志文件，不推送通知，只返回结果
    if not test_mode:
        now = datetime.now()
        log_entry = {
            "task_id": task_id,
            "time": now.isoformat(),
            "result": final_result,
            "last_success": final_result.get("last_success", -1) if final_result else -1
        }
        (work_dir / "latest_result.json").write_text(
            json.dumps(log_entry, ensure_ascii=False, indent=2))

    ok = _is_success(final_result) if final_result else False
    _write_result(work_dir, task_id, ok, final_result)

    envelop.payload = {"ok": True, "result": final_result}
    return envelop

# ============================================================
# 🔥 执行工作流（含自动修复）
# ============================================================

async def _execute_workflow(agent, workflow, work_dir, task_id, test_mode=False):
    plugin_dir = work_dir / "plugins"
    prev_results = []
    final_result = None
    steps = workflow.get("steps", [])
    total_steps = len(steps)
    last_success = -1

    cache_path = work_dir / "step_cache.json"
    cached_results = {}
    # 🔥 测试模式：不使用缓存，每次全新执行
    if not test_mode and cache_path.exists():
        try:
            cached_results = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"[os_agent] 📦 加载缓存: {len(cached_results)} 个步骤已成功")
        except:
            pass

    print(f"[os_agent] 📋 执行 {total_steps} 个步骤")

    for i, step in enumerate(steps):
        plugin_name = Path(step.get("receiver", "")).stem

        # 🔥 测试模式：跳过缓存逻辑
        if not test_mode and plugin_name in cached_results and cached_results[plugin_name].get("ok") is True:
            print(f"[os_agent] ⏭️ 步骤 {i+1} ({plugin_name}) 已缓存，跳过")
            prev_results.append(cached_results[plugin_name])
            last_success = i
            final_result = cached_results[plugin_name]
            continue

        print(f"[os_agent] 📍 步骤 {i+1}/{total_steps}")
        
        # 🔥 测试模式：不推送前端通知
        if not test_mode:
            await _push(agent, task_id, "step_start", {
                "step": i + 1, "total": total_steps,
                "plugin": step.get("receiver", "unknown")
            })

        receiver = step.get("receiver", "")
        if not receiver:
            prev_results.append({"ok": False, "error": "缺少 receiver"})
            continue

        if step.get("condition") and not _eval_condition(step["condition"], prev_results):
            print(f"[os_agent] ⏭️ 步骤 {i+1} 条件不满足，跳过")
            continue

        plugin_path = plugin_dir / f"{plugin_name}.py"
        if not plugin_path.exists():
            print(f"[os_agent] ❌ 插件不存在: {plugin_path}")
            prev_results.append({"ok": False, "error": f"插件不存在: {plugin_name}"})
            continue

        if prev_results:
            step["payload"]["params"]["prev"] = prev_results[-1]
        payload = _resolve_refs(step["payload"], prev_results)
        env = core.Envelop(sender="builtins/agents/os_agent", receiver=receiver, payload=payload)

        print(f"[os_agent] ▶️ 执行: {plugin_name}, action={step['payload'].get('action', 'default')}")

        success = False
        # 🔥 测试模式：减少重试次数，快速失败
        max_retries = 1 if test_mode else 3
        last_error_msg = ""

        for attempt in range(max_retries + 1):
            execute_fn, load_error = _load_plugin(plugin_path, plugin_name)
            if not execute_fn:
                last_error_msg = load_error or f"加载失败: {plugin_name}"
                print(f"[os_agent] ❌ {last_error_msg}")
                break


            try:
                # 🔥 测试模式：更短的超时
                step_timeout = 60 if test_mode else 180
                res = await asyncio.wait_for(execute_fn(env, agent), timeout=step_timeout)
                result_data = getattr(res, 'payload', res) if res else {"ok": False, "error": "无返回"}
            except asyncio.TimeoutError:
                last_error_msg = "步骤执行超时"
                print(f"[os_agent] ⏱️ {plugin_name} 超时")
                result_data = {"ok": False, "error": last_error_msg}
            except Exception as e:
                last_error_msg = f"{type(e).__name__}: {e}"
                print(f"[os_agent] ❌ {plugin_name} 异常: {last_error_msg[:150]}")
                result_data = {"ok": False, "error": last_error_msg}

            if _is_success(result_data):
                prev_results.append(result_data)
                final_result = result_data
                last_success = i
                success = True
                # 🔥 测试模式：不写缓存
                if not test_mode:
                    cached_results[plugin_name] = result_data
                    cache_path.write_text(json.dumps(cached_results, ensure_ascii=False, indent=2))
                print(f"[os_agent] ✅ 步骤 {i+1} 成功")
                if not test_mode:
                    await _push(agent, task_id, "step_done", {
                        "step": i + 1, "total": total_steps,
                        "plugin": plugin_name, "ok": True
                    })
                break

            last_error_msg = _get_error_message(result_data)
            print(f"[os_agent] ⚠️ {plugin_name} 失败: {last_error_msg[:100]}")

            if "RgbColor" in last_error_msg or "RGBColor" in last_error_msg:
                code = plugin_path.read_text(encoding='utf-8')
                code = code.replace("RgbColor", "RGBColor")
                plugin_path.write_text(code, encoding='utf-8')
                print(f"[os_agent] 🔧 自动修复 RgbColor → RGBColor")
                continue

            if attempt < max_retries and _is_retryable_error(last_error_msg):
                print(f"[os_agent] 🔄 重试 {attempt+1}/{max_retries}")
                await asyncio.sleep(2)
                continue

            # 🔥 测试模式：不触发修复循环（由 dev_agent._test_and_fix 统一处理）
            if test_mode:
                print(f"[os_agent] 🧪 测试模式：不触发修复，直接返回错误")
                break

            print(f"[os_agent] 🔧 通知 Dev Agent 修复: {plugin_name}")
            fingerprint = _extract_failure_fingerprint(last_error_msg)
            if fingerprint:
                _record_failure_pattern(fingerprint, plugin_name, False)

            fix_success = await _notify_dev_to_fix(
                agent, work_dir, task_id, plugin_name, last_error_msg, step,
                steps, i, cached_results
            )

            if fix_success:
                execute_fn, _ = _load_plugin(plugin_path, plugin_name)
                if execute_fn:
                    try:
                        res = await asyncio.wait_for(execute_fn(env, agent), timeout=180)
                        result_data = getattr(res, 'payload', res) if res else {"ok": False, "error": "无返回"}
                        if _is_success(result_data):
                            prev_results.append(result_data)
                            final_result = result_data
                            last_success = i
                            success = True
                            cached_results[plugin_name] = result_data
                            cache_path.write_text(json.dumps(cached_results, ensure_ascii=False, indent=2))
                            print(f"[os_agent] ✅ 步骤 {i+1} 修复后成功")
                            if not test_mode:
                                await _push(agent, task_id, "step_done", {
                                    "step": i + 1, "total": total_steps,
                                    "plugin": plugin_name, "ok": True
                                })
                            break
                    except Exception as e2:
                        print(f"[os_agent] 修复后执行仍失败: {e2}")
            else:
                print(f"[os_agent] ❌ Dev Agent 修复失败")

        if not success:
            print(f"[os_agent] ❌ 步骤 {i+1} 失败，停止执行")
            if not test_mode:
                await _push(agent, task_id, "step_failed", {
                    "step": i + 1, "total": total_steps,
                    "plugin": plugin_name,
                    "error": last_error_msg
                })
            final_result = {"ok": False, "error": last_error_msg, "last_success": last_success}
            break

    if final_result:
        final_result["last_success"] = last_success

    print(f"[os_agent] 🏁 工作流执行完成，last_success={last_success}")
    return final_result





def _is_retryable_error(e):
    msg = str(e).lower()
    retryable_keywords = ["timeout", "connection", "refused", "reset", "temporary", "retry"]
    return any(k in msg for k in retryable_keywords)

# ============================================================
# 🔥 插件失败自学习系统
# ============================================================

import hashlib
import json
from pathlib import Path
from datetime import datetime

FAILURE_PATTERNS_PATH = Path("data/agents/failure_patterns.json")


def _normalize_error(error_msg: str) -> str:
    """归一化错误信息，去掉变量部分，只保留结构"""
    import re
    normalized = re.sub(r'File ".*?", line \d+', 'File "<path>", line <n>', error_msg)
    normalized = re.sub(r'0x[0-9a-fA-F]+', '0x<addr>', normalized)
    normalized = re.sub(r'\d+', '<n>', normalized)
    normalized = re.sub(r"'[^']*'", "'<var>'", normalized)
    normalized = re.sub(r'"[^"]*"', '"<var>"', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def _extract_failure_fingerprint(error_msg: str) -> dict:
    """从错误信息中提取失败指纹，相似错误自动合并"""
    if not error_msg:
        return None
    normalized = _normalize_error(error_msg)
    short_hash = hashlib.md5(normalized[:300].encode()).hexdigest()[:8]
    return {
        "fingerprint": f"auto_{short_hash}",
        "hint": error_msg[:300],
        "normalized": normalized[:200],
    }


def _record_failure_pattern(fingerprint: dict, plugin_name: str, fix_success: bool):
    """记录失败模式，相似错误合并权重"""
    FAILURE_PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    patterns = []
    if FAILURE_PATTERNS_PATH.exists():
        try:
            patterns = json.loads(FAILURE_PATTERNS_PATH.read_text(encoding='utf-8'))
        except:
            patterns = []
    
    existing = next(
        (p for p in patterns if p["fingerprint"] == fingerprint["fingerprint"]),
        None
    )
    
    if existing:
        existing["count"] += 1
        existing["last_seen"] = datetime.now().isoformat()
        existing["status"] = "fixed" if fix_success else "unresolved"
        existing["last_plugin"] = plugin_name
    else:
        patterns.append({
            "fingerprint": fingerprint["fingerprint"],
            "hint": fingerprint["hint"],
            "normalized": fingerprint.get("normalized", ""),
            "count": 1,
            "first_seen": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "status": "fixed" if fix_success else "unresolved",
            "last_plugin": plugin_name
        })
    
    patterns.sort(key=lambda x: (
        0 if x["status"] == "unresolved" else 1,
        -x["count"],
        x["last_seen"]
    ))
    
    patterns = patterns[:50]
    FAILURE_PATTERNS_PATH.write_text(
        json.dumps(patterns, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def _get_active_warnings(limit: int = 5) -> str:
    """获取权重最高的失败模式，供 dev_agent 生成时注入提示"""
    if not FAILURE_PATTERNS_PATH.exists():
        return ""
    try:
        patterns = json.loads(FAILURE_PATTERNS_PATH.read_text(encoding='utf-8'))
    except:
        return ""
    if not patterns:
        return ""
    
    selected = patterns[:limit]
    hints = []
    for p in selected:
        tag = "🔴" if p["status"] == "unresolved" else "🟡"
        hints.append(f"{tag} {p['hint'][:200]} (已出现{p['count']}次)")
    
    return "\n\n⚠️【系统最近踩过的坑，务必避免】\n" + "\n".join(f"- {h}" for h in hints)

def _write_result(work_dir, task_id, ok, result):
    result_file = work_dir / "output" / "result.txt"
    result_file.parent.mkdir(exist_ok=True)
    status = "完成" if ok else "失败"

    if isinstance(result, dict):
        result_str = json.dumps(result, ensure_ascii=False, indent=2)
    elif isinstance(result, str):
        result_str = result
    else:
        result_str = str(result)

    result_file.write_text(
        f"任务ID: {task_id}\n"
        f"状态: {status}\n"
        f"时间: {datetime.now().isoformat()}\n"
        f"最后成功步骤: {result.get('last_success', -1) if isinstance(result, dict) else -1}\n"
        f"\n{result_str}",
        encoding='utf-8'
    )


# ========== 修订后的 _load_plugin ==========

# 🔥 白名单：明确允许的模块（放开大部分，只限制真正危险的）
_RESTRICTED_MODULES = {
    'ctypes',           # 可以调用任意 C 函数，危险
    'importlib',        # 可以绕过模块限制
}

# 🔥 危险函数监控（记录但不阻止）
_DANGEROUS_CALLS = {
    'os.system': '执行系统命令',
    'subprocess.call': '执行子进程',
    'subprocess.run': '执行子进程',
    'subprocess.Popen': '启动子进程',
    'os.remove': '删除文件',
    'os.unlink': '删除文件',
    'shutil.rmtree': '递归删除目录',
    'os.chmod': '修改文件权限',
    'os.chown': '修改文件所有者',
}

# 🔥 真正要阻止的（会破坏宿主进程）
_BLOCKED_ACTIONS = {
    'sys.exit': '禁止终止宿主进程',
    'os._exit': '禁止强制退出',
    'os.kill': '禁止发送信号（除当前进程外）',
    'signal.alarm': '禁止设置闹钟信号',
}

import sys


import traceback



def _load_plugin(plugin_path, plugin_name):
    """
    安全加载插件模块
    
    安全策略（修订版）：
    1. ✅ 允许 subprocess — 保留插件的系统调用能力
    2. ✅ 允许 os 大部分操作 — 保留文件系统操作能力  
    3. ❌ 阻止 sys.exit / os._exit — 防止终止宿主进程
    4. ⚠️  记录危险操作 — 用于审计，不阻止执行
    5. ❌ 阻止 ctypes — 防止绕过所有限制
    """
    try:
        source_code = plugin_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"[os_agent] ❌ 无法读取插件源码: {e}")
        return None
    
    # 🔥 静态检查：只记录，不阻止
    for pattern, desc in _DANGEROUS_CALLS.items():
        if re.search(pattern.replace('.', r'\.') + r'\s*\(', source_code):
            print(f"[os_agent] 📝 检测到: {desc}（允许执行）")
    
    # 🔥 静态检查：阻止破坏宿主进程的操作
    for pattern, desc in _BLOCKED_ACTIONS.items():
        if re.search(pattern.replace('.', r'\.') + r'\s*\(', source_code):
            print(f"[os_agent] ⚠️ 检测到危险操作: {desc}，将在运行时拦截")
    
    try:
        spec = importlib.util.spec_from_file_location(plugin_name, plugin_path)
        mod = importlib.util.module_from_spec(spec)
        
        # 🔥 只拦截真正危险的操作
        original_sys = sys.modules.get('sys')
        
        class SafeSys:
            """拦截 sys.exit，其他操作放行"""
            def __getattr__(self, name):
                if name == 'exit':
                    def blocked_exit(code=0):
                        raise SystemExit(f"插件调用 sys.exit({code}) 被拦截，宿主进程继续运行")
                    return blocked_exit
                return getattr(sys, name)
        
        safe_sys = SafeSys()
        sys.modules['sys'] = safe_sys
        
        try:
            spec.loader.exec_module(mod)
        finally:
            # 🔥 恢复
            if original_sys:
                sys.modules['sys'] = original_sys
        
        if hasattr(mod, "execute"):
            return mod.execute, None
        else:
            print(f"[os_agent] ⚠️ 插件缺少 execute 函数: {plugin_name}")
            return None, "缺少 execute 函数"
            
    except SystemExit as e:
        # 🔥 捕获 sys.exit 抛出的 SystemExit
        print(f"[os_agent] ⚠️ 插件尝试退出，已拦截: {e}")
        return None, f"插件尝试退出: {e}"
        
    except ModuleNotFoundError as e:
        msg = str(e)
        pkg = None
        if "'" in msg:
            pkg = msg.split("'")[1]
        elif '"' in msg:
            pkg = msg.split('"')[1]
        
        if pkg:
            if _ensure_package(pkg):
                return _load_plugin(plugin_path, plugin_name)
        
        print(f"[os_agent] ❌ 模块未找到: {pkg or msg}")
        return None, f"模块未找到: {pkg or msg}"

        
    except ImportError as e:
        msg = str(e)
        code = plugin_path.read_text(encoding='utf-8')
        fixed = False
        
        if "RgbColor" in msg:
            code = code.replace("RgbColor", "RGBColor")
            fixed = True
        
        if fixed:
            print(f"[os_agent] 🔧 自动修复: {plugin_name}")
            plugin_path.write_text(code, encoding='utf-8')
            return _load_plugin(plugin_path, plugin_name)
        
        print(f"[os_agent] ❌ 加载异常: {e}")
        return None, f"导入错误: {e}"
        
    except SyntaxError as e:
        print(f"[os_agent] ❌ 语法错误: {e.msg} (第 {e.lineno} 行)")
        return None, f"语法错误: {e.msg} (第 {e.lineno} 行)"
        
    except Exception as e:
        print(f"[os_agent] 加载插件异常: {e}")
        traceback.print_exc()
        return None, f"{type(e).__name__}: {e}"



def _eval_condition(condition, prev_results):
    if not prev_results:
        return False
    last = prev_results[-1]
    data = last.get("data", last)
    parts = condition.split("==")
    if len(parts) == 2:
        key = parts[0].strip().replace("prev.", "")
        return str(data.get(key, "")).lower() == parts[1].strip().lower()
    return True


def _normalize_workflow(workflow, task_id):
    """规范化 workflow 格式，兼容 tasks 格式"""
    steps = workflow.get("steps", [])
    
    if steps:
        for step in steps:
            receiver = step.get("receiver", "")
            # 去掉 .py 后缀
            if receiver.endswith(".py"):
                receiver = receiver[:-3]
                step["receiver"] = receiver
            # 去掉 plugins/ 前缀
            if receiver.startswith("plugins/"):
                receiver = receiver[8:]
                step["receiver"] = receiver
            # 如果不是 tasks/ 开头，补全
            if not receiver.startswith("tasks/"):
                step["receiver"] = f"tasks/{task_id}/plugins/{receiver}"
        return workflow
    
    tasks = workflow.get("tasks", [])
    if tasks:
        normalized_steps = []
        for task in tasks:
            plugin_name = task.get("plugin", "")
            plugin_name = plugin_name.replace(".py", "").replace("plugins/", "")
            if not plugin_name:
                continue
            normalized_steps.append({
                "receiver": f"tasks/{task_id}/plugins/{plugin_name}",
                "payload": {
                    "action": "execute",
                    "params": task.get("params", {})
                }
            })
        if normalized_steps:
            workflow["steps"] = normalized_steps
            print(f"[os_agent] 🔄 已转换 tasks 格式为 steps 格式")
            return workflow
    
    return workflow


def _ensure_workflow(work_dir, task_id, workflow):
    """确保 workflow 有 steps，如果没有则自动生成"""
    steps = workflow.get("steps", [])
    
    if steps:
        # 🔥 清理 receiver
        for step in steps:
            receiver = step.get("receiver", "")
            if receiver.endswith(".py"):
                receiver = receiver[:-3]
                step["receiver"] = receiver
            if receiver.startswith("plugins/"):
                receiver = receiver[8:]
                step["receiver"] = receiver
            if not receiver.startswith("tasks/"):
                step["receiver"] = f"tasks/{task_id}/plugins/{receiver}"
        return workflow
    
    normalized = _normalize_workflow(workflow, task_id)
    if normalized.get("steps"):
        return normalized
    
    plugin_dir = work_dir / "plugins"
    plugin_files = list(plugin_dir.glob("*.py"))
    if plugin_files:
        steps = []
        for pf in plugin_files:
            steps.append({
                "receiver": f"tasks/{task_id}/plugins/{pf.stem}",
                "payload": {"action": "execute", "params": {}}
            })
        workflow["steps"] = steps
        print(f"[os_agent] 🔄 自动生成 steps，共 {len(steps)} 步")
    
    return workflow


def _resolve_refs(payload, prev_results):
    """
    安全地解析 prev.xxx 引用
    
    规则：
    - 精确匹配：只有字符串值恰好等于 "prev.字段名" 时才替换
    - 嵌套递归：递归处理 dict 和 list
    - 保留 prev 对象：params.prev 中的 prev 键原样保留
    """
    if not prev_results:
        return payload
    
    last = prev_results[-1]
    # 优先从 data 字段取，兼容直接返回字段的情况
    prev_data = last.get("data", last)
    
    def _resolve_value(value):
        """递归解析单个值中的 prev.xxx 引用"""
        if isinstance(value, str):
            # 🔥 精确匹配：整个字符串恰好是 "prev.xxx" 才替换
            if value.startswith("prev."):
                key = value[5:]  # 去掉 "prev."
                if key in prev_data:
                    return prev_data[key]
            return value
        elif isinstance(value, dict):
            return {k: _resolve_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_resolve_value(v) for v in value]
        else:
            return value
    
    # 深拷贝并递归解析

    resolved = copy.deepcopy(payload)
    
    # 递归处理 payload 中所有值
    resolved = _resolve_value(resolved)
    
    # 🔥 保留 prev 对象引用：如果 payload.params 中有 prev 键，原样保留
    if isinstance(resolved, dict) and "params" in resolved:
        if isinstance(resolved["params"], dict) and "prev" in resolved["params"]:
            # prev 对象已经在上面被递归处理过，保留引用
            pass
        elif isinstance(payload, dict) and "params" in payload:
            if isinstance(payload["params"], dict) and "prev" in payload["params"]:
                resolved.setdefault("params", {})["prev"] = payload["params"]["prev"]
    
    return resolved