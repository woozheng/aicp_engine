# plugins/saver.py
"""
代码保存服务 — 从 AI 回复中提取代码块并保存到本地
Route: POST /api/saver/save
"""

import re
import uuid
from pathlib import Path
from typing import List, Dict, Any


def help():
    return {
        "route": "/api/saver/save",
        "input": {"text": "AI 回复的完整文本"},
        "output": {"saved": ["file1", "file2"], "count": 0, "errors": []},
        "description": "从 AI 回复中提取代码块并保存到本地，支持多种格式和容错",
    }


async def execute(envelop, agent):
    try:
        text = envelop.payload.get("text", "")
        if not text:
            envelop.payload = {"ok": False, "error": "No text provided", "saved": [], "count": 0}
            return envelop

        result = extract_and_save_all(text)
        envelop.payload = {"ok": True, **result}
        return envelop

    except Exception as e:
        envelop.payload = {"ok": False, "error": str(e), "saved": [], "count": 0}
        return envelop


def _clean_code(code: str) -> str:
    # 去掉末尾残留的 === xxx（可能不完整，如 === end 或 ===END 等）
    code = re.sub(r'\n?\s*={2,}\s*\w*\s*=*\s*$', '', code, flags=re.IGNORECASE)
    # 统一换行符
    code = code.replace('\r\n', '\n').replace('\r', '\n')
    # 去掉每行末尾空格
    code = '\n'.join(line.rstrip() for line in code.split('\n'))
    return code.strip()


def extract_and_save_all(text: str) -> Dict[str, Any]:
    """提取所有格式的代码块并保存"""
    saved = []
    errors = []

    patterns = [
        # ============================================================
        # 0. 通用 AICP 块（不限类型，最高优先级）
        #    匹配：=== TYPE: path === ... === END ===
        # ============================================================
        {
            'pattern': r'===\s*(\w+):\s*(\S+)\s*===\s*\n(.*?)\n=== \w+ ===',
            'type': 'generic',
            'path_group': 2,
            'code_group': 3,
        },
        # ============================================================
        # 1. 未闭合的 AICP 块（漏写 === END ===）
        #    匹配：=== TYPE: path === ... (直到下一个块或文本结束)
        # ============================================================
        {
            'pattern': r'===\s*(PLUGIN|HTML|CSS|JS|APP|YAML):\s*(\S+)\s*===\s*\n(.*?)(?====\s*\w+:\s*\S+\s*===|$)',
            'type': 'loose',
            'path_group': 2,
            'code_group': 3,
        },
        # ============================================================
        # 2. Markdown 代码块 + 注释路径
        # ============================================================
        {
            'pattern': r'```python\s*\n#\s*(plugins/[^\s]+\.py)\s*\n(.*?)\n```',
            'type': 'plugin',
            'path_group': 1,
            'code_group': 2,
        },
        {
            'pattern': r'```html\s*\n<!--\s*(www/[^\s]+\.html)\s*-->\s*\n(.*?)\n```',
            'type': 'html',
            'path_group': 1,
            'code_group': 2,
        },
        # ============================================================
        # 3. Markdown 代码块（无路径，自动命名）
        # ============================================================
        {
            'pattern': r'```python\s*\n(.*?)\n```',
            'type': 'auto_py',
            'path_group': None,
            'code_group': 1,
        },
        {
            'pattern': r'```html\s*\n(.*?)\n```',
            'type': 'auto_html',
            'path_group': None,
            'code_group': 1,
        },
        # ============================================================
        # 4. 带注释路径的代码（无标记块）
        # ============================================================
        {
            'pattern': r'#\s*(plugins/[^\s]+\.py)\s*\n(.*?)(?=\n#\s+\S|$)',
            'type': 'plugin_comment',
            'path_group': 1,
            'code_group': 2,
        },
    ]

    processed_positions = set()

    for p in patterns:
        for match in re.finditer(p['pattern'], text, re.DOTALL):
            pos = match.start()
            if pos in processed_positions:
                continue
            processed_positions.add(pos)

            try:
                # 提取路径
                if p['path_group'] and p['path_group'] <= match.lastindex:
                    filepath = match.group(p['path_group']).strip()
                else:
                    filepath = None

                # 提取代码
                if p['code_group'] <= match.lastindex:
                    code = match.group(p['code_group'])
                else:
                    continue

                code = _clean_code(code)
                if not code or len(code) < 10:
                    continue

                # 自动生成路径
                if not filepath:
                    filepath = _auto_name(code, p['type'])

                if not filepath:
                    continue

                # 保存
                path = Path(filepath)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(code, encoding='utf-8')
                saved.append(str(path))
                print(f"📁 Saved: {path}")

            except Exception as e:
                errors.append(f"Failed to save: {e}")

    # 打印汇总
    print(f"\n📊 Saver: {len(saved)} files saved" + (f", {len(errors)} errors" if errors else ""))
    for f in saved:
        print(f"   ✅ {f}")

    return {
        "saved": saved,
        "count": len(saved),
        "errors": errors,
        "message": f"已保存 {len(saved)} 个文件" + (f", {len(errors)} 个错误" if errors else ""),
    }


def _auto_name(code: str, block_type: str) -> str:
    """根据代码内容自动生成文件路径"""
    if block_type in ('auto_py', 'plugin', 'plugin_comment'):
        return f"plugins/auto_{uuid.uuid4().hex[:8]}.py"
    if block_type in ('auto_html', 'html'):
        return f"www/auto_{uuid.uuid4().hex[:8]}.html"
    # 内容推断
    if 'async def execute' in code or 'def help()' in code:
        return f"plugins/auto_{uuid.uuid4().hex[:8]}.py"
    if '<html' in code.lower():
        return f"www/auto_{uuid.uuid4().hex[:8]}.html"
    return ""