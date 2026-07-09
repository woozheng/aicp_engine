"""Dev Agent — 拆解需求、生成插件、输出工作流（LLM 自主判断复杂度 + 迭代修复）"""
import core
import json
import re
import asyncio
from pathlib import Path

# ============================================================
# 修复 3：PROTOCOL 强化 END 标记
# 位置：plugins/builtins/agents/dev_agent.py
# ============================================================

PROTOCOL = """
## 角色
- 精通 Python 3.11+ 的后端工程师，代码严谨、紧凑、可运行，不回复任何文字描述

【铁律】
!!! 所有 import 写在文件最前面
!!! f-string 里禁止反斜杠
!!! 禁止空壳插件
!!! === PLUGIN: 块末尾必须有 === END ===
!!! 直接输出 === PLUGIN: 开头
!!! 禁止重定向 sys.stdout / sys.stderr（会导致后续插件崩溃）
!!! locale 编码错误用 encoding='utf-8' 或修改 strftime 解决，不要碰 sys.stdout

### ⚠️ 插件签名
async def execute(envelop, agent):
    params = envelop.payload.get("params", {})
    envelop.payload = {"ok": True, "data": {...}}
    return envelop

### ⚠️ 中文编码
- open() 必须加 encoding='utf-8'
- 中文直接写，禁止 \\uXXXX 转义
- strftime 用 '%Y年%m月%d日'
- 禁止重定向 sys.stdout/sys.stderr

### ⚠️ 文件操作
- 读取前检查大小，>1MB 跳过内容预览
- 读取用 f.read_bytes()[:65536]
- 大文件写入用 await asyncio.get_event_loop().run_in_executor(None, lambda: ...)

### ⚠️ 网络请求
- 用 requests，设置 timeout=15
- 下载文件用 requests.get(url, stream=True) + shutil.copyfileobj

### ⚠️ LLM 调用
- 用 agent.llm.chat([{"role": "user", "content": "..."}]) 做推理
- 返回 str，不是 dict
- 不要自己加 try/except 保护

### ⚠️ 多插件协作
- 用 params.get("prev", {}).get("data", {}) 获取上游输出
- 字段名必须与契约完全一致
- 上游数据缺失返回 {"ok": False, "error": "缺少字段: xxx"}

### ⚠️ JSON 解析
- 解析前清洗：re.sub(r'```\\w*\\n?', '', text)

### ⚠️ python-pptx 中文
- p.font.name = 'SimSun'

### ⚠️ matplotlib 中文
- plt.rcParams['font.sans-serif'] = ['SimHei']
- plt.rcParams['axes.unicode_minus'] = False

### ⚠️ 代码风格
- 不写注释、不写 docstring，变量名尽量短
- 能一行写完的不换行，简单 if/else 用三元表达式

### ⚠️ 输出格式
=== PLUGIN: 文件名.py ===
代码
=== END ===

=== WORKFLOW: workflow.json ===
{"steps": [{"receiver": "tasks/{task_id}/plugins/插件名", "payload": {"action": "execute", "params": {}}}]}
=== END ===

=== SCHEDULE: schedule.json ===
{"cron": "0 8 * * *", "notify": {"email": "通知邮箱"}}
=== END ===
"""

def help():
    return {
        "route": "/api/builtins/agents/dev_agent",
        "actions": {"build": {"params": {"task": "需求", "work_dir": "工作目录"}}}
    }


async def execute(envelop, agent):
    params = envelop.payload.get("params", {})
    task = params.get("task", "")
    work_dir = Path(params.get("work_dir", "data/agents/tasks/default"))
    test_mode = params.get("test_mode", False)
    force_single_plugin = params.get("force_single_plugin", False)
    force_plugin_name = params.get("force_plugin_name", "")

    print(f"[dev_agent] work_dir: {work_dir}")
    print(f"[dev_agent] workflow_path: {work_dir / 'workflow.json'}")

    if not task:
        envelop.payload = {"ok": False, "error": "缺少 task"}
        return envelop

    llm = agent.llm
    if not llm:
        envelop.payload = {"ok": False, "error": "LLM 不可用"}
        return envelop

    task_id = work_dir.name
    route_prefix = f"tasks/{task_id}/plugins"
    system_prefix = PROTOCOL + f"\n当前: task_id={task_id}, work_dir={work_dir}, 路由前缀={route_prefix}"
    from plugins.builtins.agents.os_agent import _get_active_warnings
    system_prefix += _get_active_warnings()

    # ============================================================
    # 🔥 修复模式：只修一个插件，跳过复杂度判断
    # ============================================================
    if force_single_plugin and force_plugin_name:
        print(f"[dev_agent] 🔧 修复模式: {force_plugin_name}")
        context = f"{task}\n\n只输出 === PLUGIN: {force_plugin_name} === 块，末尾必须有 === END ===。"
        raw = await llm.chat(
            messages=[{"role": "system", "content": system_prefix}, {"role": "user", "content": context}],
            role="coding", temperature=0.1, tools=[], tool_choice="none"
        )
        blocks = _parse_blocks(raw)
        if not blocks:
            code = _clean_code(raw)
            blocks = [{"type": "PLUGIN", "file": force_plugin_name, "code": code}]

        plugin_dir = work_dir / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        for block in blocks:
            if block["type"] == "PLUGIN":
                file_name = Path(block["file"]).name
                (plugin_dir / file_name).write_text(_clean_code(block["code"]), encoding="utf-8")
                print(f"[dev_agent] 🔧 修复保存: {file_name}")

        envelop.payload = {"ok": True, "plugin_count": len(blocks)}
        return envelop

    # ============================================================
    # 🔥 第 0 步：前置查 API
    # ============================================================
    api_info = await _analyze_api_need(task, agent.llm)
    if api_info.get("apis"):
        system_prefix += f"\n\n✅ 系统已接入以下外部能力：{json.dumps(api_info['apis'], ensure_ascii=False)}"

    # ============================================================
    # 1. 🔥 判断复杂度
    # ============================================================
    analysis = await _estimate_complexity(llm, task, api_info,system_prefix)
    complexity = analysis["complexity"]
    plugin_specs = analysis["plugins"]
    need_workflow = analysis["workflow"]
    need_schedule = analysis["schedule"]
    print(f"[dev_agent] 复杂度: {complexity}, 插件数: {len(plugin_specs)}, 预估总行数: {analysis['estimated_total_lines']}")

    structure = {
        "plugins": [p["name"] for p in plugin_specs],
        "workflow": need_workflow,
        "schedule": need_schedule,
        "_specs": plugin_specs,
        "_pipeline": analysis.get("pipeline", {}),  # ← 加 pipeline
    }

    if complexity >= 2:
        structure_block = {
            "type": "STRUCTURE",
            "file": "structure.json",
            "code": json.dumps(structure, ensure_ascii=False)
        }
        all_blocks = await _generate_iteratively(
            llm=llm,
            task=task,
            task_id=task_id,
            work_dir=work_dir,
            route_prefix=route_prefix,
            first_blocks=[structure_block],
            agent=agent,
            system_prefix=system_prefix
        )
    else:
        spec = plugin_specs[0] if plugin_specs else {"name": "main.py", "description": task, "output": {}}
        context = f"任务: {task[:600]}\n描述: {spec.get('description', '')}\n预估行数: {spec.get('estimated_lines', 200)}\n推荐模块: {', '.join(spec.get('key_modules', []))}\n输出: {json.dumps(spec.get('output', {}))}\n\n只输出 === PLUGIN: {spec['name']} === 块，末尾必须有 === END ===。"
        raw = await llm.chat(messages=[{"role": "system", "content": system_prefix}, {"role": "user", "content": context}], role="coding", temperature=0.1, tools=[], tool_choice="none")
        all_blocks = _parse_blocks(raw)
        if not all_blocks:
            print(f"[dev_agent] ⚠️ 单插件解析失败，重试...")
            retry_raw = await llm.chat(messages=[{"role": "system", "content": system_prefix}, {"role": "user", "content": f"上次解析失败。请只输出 === PLUGIN: {spec['name']} === 和代码，末尾必须有 === END ===。"}], role="coding", temperature=0.1, tools=[], tool_choice="none")
            all_blocks = _parse_blocks(retry_raw)
        if not all_blocks:
            all_blocks = [{"type": "PLUGIN", "file": spec['name'], "code": _clean_code(raw)}]
            print(f"[dev_agent] ⚠️ 重试仍失败，兜底保存原始输出")

    print(f"[dev_agent] 总共 {len(all_blocks)} 个 blocks")

    # ============================================================
    # 保存
    # ============================================================
    plugin_dir = work_dir / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    for block in all_blocks:
        if block["type"] == "PLUGIN":
            file_name = Path(block["file"]).name
            (plugin_dir / file_name).write_text(_clean_code(block["code"]), encoding="utf-8")
            print(f"[dev_agent] ✅ 保存插件: {file_name}")

    # ============================================================
    # workflow.json
    # ============================================================
    workflow_path = work_dir / "workflow.json"
    for block in all_blocks:
        if block["type"] == "WORKFLOW":
            wf = _clean_json(block["code"])
            workflow_path.write_text(json.dumps(wf, ensure_ascii=False, indent=2))
            print(f"[dev_agent] ✅ 保存 workflow 到: {workflow_path}")
            break
    else:
        if plugin_specs:
            steps = []
            for p in plugin_specs:
                plugin_name = p["name"].replace(".py", "")
                if (plugin_dir / f"{plugin_name}.py").exists():
                    steps.append({
                        "receiver": f"tasks/{task_id}/plugins/{plugin_name}",
                        "payload": {"action": "execute", "params": {}}
                    })
            if steps:
                default_workflow = {"steps": steps}
                workflow_path.write_text(json.dumps(default_workflow, ensure_ascii=False, indent=2))
                print(f"[dev_agent] ✅ 自动生成 workflow 到: {workflow_path}")
        else:
            plugin_files = sorted(plugin_dir.glob("*.py"))
            if plugin_files:
                steps = [{"receiver": f"tasks/{task_id}/plugins/{pf.stem}", "payload": {"action": "execute", "params": {}}} for pf in plugin_files]
                workflow_path.write_text(json.dumps({"steps": steps}, ensure_ascii=False, indent=2))
                print(f"[dev_agent] ✅ 自动生成 workflow 到: {workflow_path}")

    # ============================================================
    # schedule.json
    # ============================================================
    schedule_path = work_dir / "schedule.json"
    for block in all_blocks:
        if block["type"] == "SCHEDULE":
            try:
                schedule_config = json.loads(block["code"])
                schedule_config["task_id"] = task_id
                schedule_config["enabled"] = True
                schedule_path.write_text(json.dumps(schedule_config, ensure_ascii=False, indent=2))
                print(f"[dev_agent] ✅ 保存 schedule 到: {schedule_path}")
            except Exception as e:
                print(f"[dev_agent] ⚠️ schedule 保存失败: {e}")
            break

    # ============================================================
    # 测试-修复循环
    # ============================================================
    if complexity >= 2 and test_mode:
        print(f"[dev_agent] 🔧 开始测试-修复循环...")
        fixed = await _test_and_fix(agent, work_dir, task_id, llm, complexity,system_prefix)
        if not fixed:
            envelop.payload = {
                "ok": False,
                "error": "测试-修复循环失败",
                "plugin_count": len([b for b in all_blocks if b["type"] == "PLUGIN"])
            }
            return envelop
        print(f"[dev_agent] ✅ 测试-修复循环完成")

    envelop.payload = {
        "ok": True,
        "workflow_path": str(workflow_path) if workflow_path.exists() else "",
        "plugin_count": len([b for b in all_blocks if b["type"] == "PLUGIN"]),
        "work_dir": str(work_dir)
    }

    return envelop

    # if envelop.payload.get("ok"):
    #     try:
    #         from plugins.builtins.knowledge_base.client import save_to_kb_async
            
    #         # 🔥 存 raw，肯定 > 50 字符
    #         save_to_kb_async(
    #             agent,
    #             content=json.dumps([b["code"][:500] for b in all_blocks if b["type"] == "PLUGIN"], ensure_ascii=False),
    #             title=f"DevAgent: {task[:50]}",
    #             source="DevAgent",
    #             type="code_project",
    #             metadata={
    #                 "task": task[:200],
    #                 "work_dir": str(work_dir),
    #                 "blocks": len(all_blocks)
    #             }
    #         )
    #     except Exception as e:
    #         print(f"[dev_agent] 知识库存入失败: {e}")
    
    # return envelop

def _clean_json_response(raw: str) -> str:
    """从 LLM 响应中提取 JSON，去掉思考过程和 markdown 标记"""
    import re
    raw = raw.strip()
    
    # 去掉 markdown 代码块
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    
    # 策略1：匹配特定的 need 字段模式（API 分析响应）
    match = re.search(r'\{[^{}]*"need"\s*:\s*(true|false)[^{}]*\}', raw, re.IGNORECASE)
    if match:
        return match.group()
    
    # 策略2：匹配特定的 apis 字段模式（API 匹配响应）
    match = re.search(r'\{\s*"apis"\s*:\s*\[.*?\]\s*\}', raw, re.DOTALL)
    if match:
        return match.group()
    
    # 策略3：使用括号计数法找第一个完整的 JSON 对象
    first_brace = raw.find('{')
    if first_brace == -1:
        return raw.strip()
    
    depth = 0
    in_string = False
    escape_next = False
    
    for i in range(first_brace, len(raw)):
        char = raw[i]
        
        if escape_next:
            escape_next = False
            continue
        
        if char == '\\':
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if not in_string:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    # 找到完整的 JSON 对象
                    json_candidate = raw[first_brace:i+1]
                    # 验证是否是合法 JSON
                    try:
                        json.loads(json_candidate)
                        return json_candidate
                    except json.JSONDecodeError:
                        # 尝试修复常见问题
                        fixed = _try_fix_json(json_candidate)
                        if fixed:
                            return fixed
                        # 继续搜索下一个 {
                        first_brace = raw.find('{', i+1)
                        if first_brace == -1:
                            break
                        depth = 0
                        i = first_brace
                        continue
    
    # 策略4：降级为贪婪匹配（保持向后兼容）
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        return match.group()
    
    return raw.strip()


def _try_fix_json(json_str: str) -> str:
    """尝试修复常见的 JSON 格式错误"""
    import re
    
    fixed = json_str
    
    # 修复1：单引号 → 双引号（但要避免字符串内的单引号）
    # 简单处理：替换键的单引号
    fixed = re.sub(r"'(\w+)'(\s*):", r'"\1"\2:', fixed)
    
    # 修复2：Python 布尔值
    fixed = fixed.replace("True", "true").replace("False", "false").replace("None", "null")
    
    # 修复3：尾部多余逗号
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r',\s*]', ']', fixed)
    
    # 修复4：缺少引号的键名
    fixed = re.sub(r'(\{|,)\s*(\w+)\s*:', r'\1"\2":', fixed)
    
    try:
        json.loads(fixed)
        return fixed
    except json.JSONDecodeError:
        return None
# ============================================================
# 🔥 新增：复杂度判断
# ============================================================
async def _analyze_api_need(task, llm):
    """分析任务是否需要外部 API，直接匹配能力索引"""
    import json
    from pathlib import Path

    # 加载能力索引
    index_path = Path("data/capability_index.json")
    if not index_path.exists():
        return {"need_external": False, "apis": []}

    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries = index.get("entries", [])

    if not entries:
        return {"need_external": False, "apis": []}

    # 把能力索引塞给 LLM
    prompt = f"""
你是一个需求分析助手。根据用户需求，从系统已接入的能力列表中选出匹配的 API。

系统已接入的能力：
{json.dumps(entries, ensure_ascii=False, indent=2)}

用户需求：
{task}

规则：
1. 如果需求不需要任何外部 API，返回 {{"apis": []}}
2. 如果需要，返回匹配的 API 条目（从上面的能力列表中选取）
3. 只输出 JSON，格式：{{"apis": [条目1, 条目2]}}

直接输出 JSON，不要任何额外文字。
"""

    raw = await llm.chat(messages=[{"role": "user", "content": prompt}], role="coding")
    raw = _clean_json_response(raw)

    try:
        result = json.loads(raw)
        apis = result.get("apis", [])
        if apis:
            return {"need_external": True, "apis": apis}
        else:
            return {"need_external": False, "apis": []}
    except Exception as e:
        print(f"[dev_agent] API 匹配失败: {e}")
        return {"need_external": False, "apis": []}

async def _estimate_complexity(llm, task, api_info, system_prefix=""):
    prompt = f"""
分析以下任务，输出完整设计方案 JSON。根据任务实际复杂度决定 complexity 值。

任务：{task}
{'系统已接入的外部能力：' + json.dumps(api_info.get('apis', [])) if api_info.get('apis') else ''}

═══════════════════════════════════════
复杂度判断标准（根据任务选择，不要默认 2）
═══════════════════════════════════════

complexity=1 — 单插件即可完成：
  · 单一操作、单一输出
  · 不需要多步骤协作
  · 示例："帮我查天气"、"读取文件内容并显示"、"生成一份报告"

complexity=2 — 需要 2-3 个插件协作：
  · 需要先采集再分析
  · 有上下游数据依赖
  · 示例："扫描目录并生成分析报告"（扫描→分析）、"抓取数据并生成图表"（抓取→分析→绘图）

complexity=3 — 需要 4+ 个插件或复杂依赖：
  · 多级数据处理流水线
  · 多个独立子系统
  · 示例："搭建完整的数据分析平台"（采集→清洗→分析→可视化→报告）

═══════════════════════════════════════
输出格式
═══════════════════════════════════════

{{
  "complexity": 1,
  "reason": "单插件即可完成，无需拆分",
  "estimated_total_lines": 200,
  "pipeline": {{
    "description": "整体流程的一句话描述",
    "steps": [
      {{
        "step": 1,
        "name": "main.py",
        "what_it_does": "具体做什么",
        "produces": "最终产物"
      }}
    ]
  }},
  "plugins": [
    {{
      "name": "main.py",
      "description": "插件职责的一句话描述",
      "output": {{"result": "string"}},
      "estimated_lines": 200,
      "key_modules": ["requests"]
    }}
  ],
  "workflow": true,
  "schedule": false
}}

═══════════════════════════════════════
路径处理规则（重要）
═══════════════════════════════════════

如果用户任务中包含明确的文件路径或目录路径：
  → 在插件代码中硬编码该路径，如 TARGET_DIR = r"E:\\psagnet"
  → 不要设计成从 params 读取路径参数
  → params 可能为空，硬编码路径最可靠

如果用户上传了文件：
  → 从 input_dir 读取

如果两者都没有：
  → 从 params 获取或使用默认值

═══════════════════════════════════════
规则
═══════════════════════════════════════

- 字段名必须与 plugins 里的 input/output 完全一致
- 第一个插件没有 input，后续插件必须有 input 和 output
- 插件名必须带 .py 后缀
- 只输出 JSON，不要其他内容
"""
    raw = await llm.chat([
        {"role": "system", "content": system_prefix},
        {"role": "user", "content": prompt}
    ])
    try:
        cleaned = _clean_json_response(raw)  # 🔥 使用已修复的提取函数
        result = json.loads(cleaned)

        complexity = result.get("complexity", 1)
        plugins = result.get("plugins", [])
        pipeline = result.get("pipeline", {})
        total_lines = result.get("estimated_total_lines", 200)
        if not plugins:
            plugins = [{"name": "main.py", "description": task, "estimated_lines": total_lines, "output": {}}]
        complexity = min(max(complexity, 1), 3)
        return {
            "complexity": complexity,
            "reason": result.get("reason", ""),
            "estimated_total_lines": total_lines,
            "pipeline": pipeline,
            "plugins": plugins,
            "workflow": result.get("workflow", True),
            "schedule": result.get("schedule", False)
        }
    except Exception as e:
        print(f"[dev_agent] 复杂度分析失败: {e}")
        return {
            "complexity": 1, "reason": "分析失败",
            "estimated_total_lines": 200,
            "pipeline": {},
            "plugins": [{"name": "main.py", "description": task, "output": {}}],
            "workflow": True, "schedule": False
        }
# ============================================================
# 🔥 新增：迭代生成（逐个生成 + 契约传递）
# ============================================================
async def _generate_iteratively(llm, task, task_id, work_dir, route_prefix, first_blocks, agent, system_prefix=""):
    """迭代生成：逐个生成插件，传递数据契约"""
    all_blocks = []
    # 🔥 用传入的 system_prefix，不要自己重建
    # system_prefix 已经在 execute 里注入过 _get_active_warnings()

    structure = None
    plugin_specs = []
    pipeline = {}
    for b in first_blocks:
        if b["type"] == "STRUCTURE":
            try:
                structure = json.loads(b["code"])
                plugin_specs = structure.get("_specs", [])
                pipeline = structure.get("_pipeline", {})
            except:
                pass
        else:
            all_blocks.append(b)

    if not structure:
        return first_blocks

    plugin_names = [p["name"] for p in plugin_specs] if plugin_specs else structure.get("plugins", [])
    need_workflow = structure.get("workflow", True)
    need_schedule = structure.get("schedule", False)

    already_done = [b["file"] for b in all_blocks if b["type"] == "PLUGIN"]
    remaining_plugins = [p for p in plugin_names if p not in already_done]

    for i, plugin_name in enumerate(remaining_plugins):
        spec = next((p for p in plugin_specs if p["name"] == plugin_name), {})
        prev_spec = plugin_specs[i-1] if i > 0 else {}

        input_fields = list(spec.get("input", {}).keys())
        output_fields = list(spec.get("output", {}).keys())
        prev_output = prev_spec.get("output", {})

        context = _build_context_with_contract(
            task, plugin_names, all_blocks, i, plugin_name,
            prev_output, input_fields, output_fields, plugin_specs, pipeline
        )

        raw = await llm.chat(
            messages=[{"role": "system", "content": system_prefix}, {"role": "user", "content": context}],
            role="coding", temperature=0.1, tools=[], tool_choice="none"
        )
        blocks = _parse_blocks(raw)
        if blocks:
            all_blocks.extend(blocks)
            print(f"[dev_agent] ✅ {plugin_name}")

            if i == 0 and blocks:
                first_code = blocks[0].get("code", "")
                actual_fields = _extract_output_fields(first_code)
                if actual_fields:
                    for p in plugin_specs:
                        if p["name"] == plugin_name:
                            p["output"] = {f: "any" for f in actual_fields}
                    if i + 1 < len(plugin_specs):
                        plugin_specs[i + 1]["input"] = {f: "any" for f in actual_fields}
                    print(f"[dev_agent] 🔒 锁定输出字段: {actual_fields}")
        else:
            print(f"[dev_agent] ⚠️ {plugin_name} 解析失败，重试...")
            retry_raw = await llm.chat(
                messages=[
                    {"role": "system", "content": system_prefix},
                    {"role": "user", "content": f"上次没有生成有效的 === PLUGIN: {plugin_name} === 块。请只输出 === PLUGIN: {plugin_name} === 和代码，末尾必须有 === END ===。"}
                ],
                role="coding", temperature=0.1, tools=[], tool_choice="none"
            )
            retry_blocks = _parse_blocks(retry_raw)
            if retry_blocks:
                all_blocks.extend(retry_blocks)
                print(f"[dev_agent] ✅ {plugin_name} (重试成功)")
            else:
                print(f"[dev_agent] ❌ {plugin_name} 重试仍失败，跳过")

    if need_workflow and not any(b["type"] == "WORKFLOW" for b in all_blocks):
        steps = []
        for p in plugin_specs:
            plugin_name = p["name"].replace(".py", "")
            steps.append({
                "receiver": f"tasks/{task_id}/plugins/{plugin_name}",
                "payload": {"action": "execute", "params": {}}
            })
        workflow_block = {
            "type": "WORKFLOW", "file": "workflow.json",
            "code": json.dumps({"steps": steps}, ensure_ascii=False)
        }
        all_blocks.append(workflow_block)
        print(f"[dev_agent] ✅ 自动生成 workflow（按契约顺序）")

    if need_schedule and not any(b["type"] == "SCHEDULE" for b in all_blocks):
        context = f"生成定时调度配置，任务：{task[:200]}"
        raw = await llm.chat(
            messages=[{"role": "system", "content": system_prefix}, {"role": "user", "content": context}],
            role="coding", temperature=0.1, tools=[], tool_choice="none"
        )
        blocks = _parse_blocks(raw)
        if blocks:
            all_blocks.extend(blocks)

    return all_blocks





# ============================================================
# 🔥 新增：带契约的上下文构建
# ============================================================
def _build_context_with_contract(task, all_plugins, existing_blocks, current_idx, current_plugin,
                                  prev_output, input_fields, output_fields, plugin_specs=None, pipeline=None):
    prev_helps = []
    for block in existing_blocks:
        if block["type"] == "PLUGIN":
            help_str = _extract_help(block["code"])
            if help_str:
                prev_helps.append(f"{block['file']}: {help_str}")

    spec = {}
    prev_spec = {}
    if plugin_specs:
        for i, p in enumerate(plugin_specs):
            if p["name"] == current_plugin:
                spec = p
                if i > 0:
                    prev_spec = plugin_specs[i-1]
                break

    # ── 全局视角 ──
    context = f"""⚠️ 原始需求: {task[:600]}

项目结构: {', '.join(all_plugins)}
当前生成: {current_plugin}（第 {current_idx + 1}/{len(all_plugins)} 个）

"""
    if pipeline:
        context += f"📋 全局视角：{pipeline.get('description', '')}\n\n"
        steps = pipeline.get("steps", [])
        for s in steps:
            marker = " ← 你在这里" if s["name"] == current_plugin else ""
            context += f"  步骤 {s['step']}「{s['name']}」{s.get('what_it_does', '')}{marker}\n"
        context += "\n"

    context += f"🔥 你的职责: {spec.get('description', '实现完整功能')}\n"
    context += f"预估行数: {spec.get('estimated_lines', 200)} 行\n"
    context += f"推荐模块: {', '.join(spec.get('key_modules', [])) if spec.get('key_modules') else '根据需求自行选择'}\n\n"

    # ── 上游数据 ──
    if prev_spec:
        upstream_output = prev_spec.get("output", {})
        current_input = spec.get("input", {})

        # 从 pipeline 里取上游的实际描述
        upstream_desc = ""
        if pipeline:
            for s in pipeline.get("steps", []):
                if s["name"] == prev_spec.get("name"):
                    upstream_desc = s.get("produces_for_downstream", "")
                    if s.get("example_output"):
                        upstream_desc += f"\n  示例数据：{json.dumps(s['example_output'], ensure_ascii=False, indent=2)}"
                    break

        context += f"""⚠️⚠️⚠️ 上游数据 — 逐字段强制遵守 ⚠️⚠️⚠️

上游插件「{prev_spec['name']}」已生成完毕。{upstream_desc}

你必须从 prev.data 中按以下字段名精确读取：
"""
        if current_input:
            for field in current_input:
                context += f"  prev.data.get(\"{field}\")\n"
        else:
            context += "  本插件是第一个插件，没有上游输入。\n"

    # ── 你的输出 ──
    context += f"""
你的输出必须包含以下字段（供下游使用）：
{json.dumps(spec.get('output', {}), ensure_ascii=False, indent=2)}
✅ 字段名必须与上述定义完全一致

现在生成 === PLUGIN: {current_plugin} === 块，末尾必须有 === END ===。
"""
    return context

# ============================================================
# 🔥 新增：测试-修复循环
# ============================================================
async def _test_and_fix(agent, work_dir, task_id, llm, complexity, system_prefix=""):
    """执行测试工作流，如果失败则让 LLM 修复"""
    max_attempts = 3 if complexity <= 2 else 5
    
    for attempt in range(max_attempts):
        print(f"[dev_agent] 🔧 测试尝试 {attempt+1}/{max_attempts}")
        
        result = await agent.system.call(core.Envelop(
            sender="builtins/agents/dev_agent",
            receiver="builtins/agents/os_agent",
            payload={
                "action": "run_task",
                "params": {"task_id": task_id, "test_mode": True}
            }
        ))
        
        if result.payload.get("ok"):
            print(f"[dev_agent] ✅ 测试通过 (尝试 {attempt+1})")
            return True
        
        error = result.payload.get("error", "未知错误")
        print(f"[dev_agent] ❌ 测试失败: {error[:200]}")
        
        fixed = await _fix_with_llm(agent, work_dir, error, llm, system_prefix)  # 🔥 传 system_prefix
        if not fixed:
            print(f"[dev_agent] ⚠️ 修复失败，继续重试...")
            continue
    
    return False


# ============================================================
# 🔥 新增：LLM 修复
# ============================================================
async def _fix_with_llm(agent, work_dir, error, llm, system_prefix=""):
    """让 LLM 根据错误日志修复代码"""
    
    log_path = work_dir / "output" / "result.txt"
    if log_path.exists():
        log = log_path.read_text(encoding="utf-8")
    else:
        log = error
    
    plugin_dir = work_dir / "plugins"
    plugin_files = list(plugin_dir.glob("*.py"))
    
    if not plugin_files:
        return False
    
    code_sections = []
    for f in plugin_files:
        code_sections.append(f"=== {f.name} ===\n{f.read_text(encoding='utf-8')}")
    code = "\n\n".join(code_sections)
    
    prompt = f"""
以下插件执行失败，请根据错误信息修复代码。

错误信息：
{log[:1500]}

插件代码：
{code[:3000]}

请修复代码中的错误，只输出修复后的 === PLUGIN: 块。
"""
    
    try:
        raw = await llm.chat([
            {"role": "system", "content": system_prefix},  # 🔥 用传入的 system_prefix
            {"role": "user", "content": prompt}
        ], role="coding", temperature=0.1)
        
        blocks = _parse_blocks(raw)
        if not blocks:
            return False
        
        for block in blocks:
            if block["type"] == "PLUGIN":
                file_name = Path(block["file"]).name
                file_path = plugin_dir / file_name
                code = _clean_code(block["code"])
                file_path.write_text(code, encoding="utf-8")
                print(f"[dev_agent] 🔧 已修复: {file_name}")
        
        return True
        
    except Exception as e:
        print(f"[dev_agent] 修复异常: {e}")
        return False


def _build_context(task, all_plugins, existing_blocks, current_idx, current_item):
    """构建生成上下文"""
    remaining = []
    done_files = []
    
    for block in existing_blocks:
        if block["type"] == "PLUGIN":
            done_files.append(block["file"])
    
    for p in all_plugins:
        if p not in done_files:
            remaining.append(p)
    
    context = f"""⚠️ 原始需求: {task[:600]}

项目结构: {', '.join(all_plugins)}
进度: {current_idx + 1}/{len(all_plugins)}

已完成: {', '.join(done_files) if done_files else '(无)'}
剩余: {', '.join(remaining) if remaining else '(无)'}

现在生成: {current_item}"""
    return context


def _extract_help(code):
    """提取插件的 help() 返回值"""
    match = re.search(r'def help\(\):.*?return\s+(\{.*?\})', code, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _parse_blocks(raw):
    print(f"[dev_agent] _parse_blocks 原始内容前500字符:\n{raw[:500]}")
    blocks = []
    lines = raw.split("\n")
    cur_type = cur_file = None
    cur_code = []

    for line in lines:
        s = line.strip()

        if s in ("```", "```python", "```json"):
            continue
        if s.startswith("<tool_call>") or s.startswith("</tool_call>"):
            continue

        if s.startswith("=== PLUGIN:") and "===" in s:
            if cur_type and cur_file and cur_code:
                blocks.append({"type": cur_type, "file": cur_file, "code": "\n".join(cur_code).strip()})
            cur_type, cur_file, cur_code = "PLUGIN", s.replace("=== PLUGIN:", "").replace("===", "").strip(), []
        elif s.startswith("=== WORKFLOW:") and "===" in s:
            if cur_type and cur_file and cur_code:
                blocks.append({"type": cur_type, "file": cur_file, "code": "\n".join(cur_code).strip()})
            cur_type, cur_file, cur_code = "WORKFLOW", s.replace("=== WORKFLOW:", "").replace("===", "").strip(), []
        elif s.startswith("=== SCHEDULE:") and "===" in s:
            if cur_type and cur_file and cur_code:
                blocks.append({"type": cur_type, "file": cur_file, "code": "\n".join(cur_code).strip()})
            cur_type, cur_file, cur_code = "SCHEDULE", s.replace("=== SCHEDULE:", "").replace("===", "").strip(), []
        elif s.startswith("=== STRUCTURE:") and "===" in s:
            if cur_type and cur_file and cur_code:
                blocks.append({"type": cur_type, "file": cur_file, "code": "\n".join(cur_code).strip()})
            cur_type, cur_file, cur_code = "STRUCTURE", s.replace("=== STRUCTURE:", "").replace("===", "").strip(), []
        elif s == "=== END ===":
            if cur_type and cur_file:
                blocks.append({"type": cur_type, "file": cur_file, "code": "\n".join(cur_code).strip()})
            cur_type = cur_file = None
            cur_code = []
        elif cur_type:
            cur_code.append(line)

    # 🔥 兜底：只在循环结束后执行一次
    if cur_type and cur_file and cur_code:
        blocks.append({"type": cur_type, "file": cur_file, "code": "\n".join(cur_code).strip()})
        print(f"[dev_agent] ⚠️ 块未闭合，兜底保存: {cur_type} {cur_file}")

    return blocks


def _clean_code(code):
    lines = code.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _clean_json(code):
    code = re.sub(r'^```\w*\n?', '', code.strip())
    code = re.sub(r'\n?```$', '', code)
    try:
        json.loads(code)
    except:
        m = re.search(r'\{[\s\S]*\}', code)
        if m:
            code = m.group()
    return json.loads(code)

def _extract_output_fields(code):
    """从生成的插件代码中提取实际输出字段名（支持嵌套JSON）"""
    import re
    import json
    
    # 策略1：匹配 envelop.payload = {...} 并解析为 JSON
    # 支持多行和嵌套
    pattern1 = r'envelop\.payload\s*=\s*(\{.*?\n\})'
    match1 = re.search(pattern1, code, re.DOTALL)
    if match1:
        try:
            # 尝试解析 JSON（处理 Python 风格的 True/False/None）
            json_str = match1.group(1)
            json_str = json_str.replace("True", "true").replace("False", "false").replace("None", "null")
            # 处理单引号
            json_str = re.sub(r"'([^']*)'", r'"\1"', json_str)
            payload = json.loads(json_str)
            data = payload.get("data", {})
            if isinstance(data, dict):
                return list(data.keys())
        except (json.JSONDecodeError, Exception):
            pass
    
    # 策略2：匹配 "data" 键后的花括号内容（支持嵌套）
    # 使用括号计数找到匹配的 }
    data_match = re.search(r'"data"\s*:\s*\{', code)
    if data_match:
        start = data_match.end() - 1  # 指向 {
        depth = 0
        end = start
        for i in range(start, len(code)):
            if code[i] == '{':
                depth += 1
            elif code[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        
        if end > start:
            inner = code[start:end]
            fields = re.findall(r'"(\w+)"\s*:', inner)
            # 过滤掉嵌套对象中的键，只取顶层
            top_fields = []
            for f in fields:
                # 检查这个字段是否在顶层（前面没有嵌套的 {）
                field_pattern = rf'"data"\s*:\s*\{{\s*"{f}"\s*:'
                if re.search(field_pattern, code):
                    top_fields.append(f)
                elif f not in top_fields:
                    top_fields.append(f)
            return top_fields
    
    # 策略3：匹配 data = {...} 变量赋值
    pattern3 = r'(\w+)\s*=\s*\{'
    matches3 = re.finditer(pattern3, code)
    for m in matches3:
        var_name = m.group(1)
        # 检查这个变量是否被用于 envelop.payload
        if re.search(rf'envelop\.payload\s*=\s*\{{\s*"ok".*"data"\s*:\s*{var_name}', code, re.DOTALL):
            # 找到该变量的定义
            var_pattern = rf'{var_name}\s*=\s*\{{\s*(.*?)\n\}}'
            var_match = re.search(var_pattern, code, re.DOTALL)
            if var_match:
                fields = re.findall(r'"(\w+)"\s*:', var_match.group(1))
                return fields
    
    return []