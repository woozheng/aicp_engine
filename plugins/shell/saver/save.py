"""
远程部署服务 — 接收代码块并保存到本地
Route: POST /api/saver/save
"""

import re
from pathlib import Path
from typing import Dict, Any


def help():
    return {
        "route": "/api/saver/save",
        "input": {"text": "=== PLUGIN/HTML/APP: path === ... === END === 格式的代码块"},
        "output": {"saved": ["file1", "file2"], "count": 0, "errors": []},
        "description": "远程部署：接收 AICP 协议格式的代码块并保存到本地",
    }


async def execute(envelop, agent):
    try:
        text = envelop.payload.get("text", "")
        if not text:
            envelop.payload = {"error": "No text provided", "saved": [], "count": 0}
            return envelop

        result = save_all(text)
        envelop.payload = result
        return envelop
    except Exception as e:
        envelop.payload = {"error": str(e), "saved": [], "count": 0}
        return envelop


def save_all(text: str) -> Dict[str, Any]:
    saved = []
    errors = []

    text = text.replace('\r\n', '\n').replace('\r', '\n')

    pattern = r'===\s*(PLUGIN|HTML|APP):\s*(\S+)\s*===\s*\n(.*?)=== END ==='

    for match in re.finditer(pattern, text, re.DOTALL):
        try:
            filepath = match.group(2).strip()
            code = match.group(3)

            if not filepath or not code:
                continue
            if '..' in filepath or filepath.startswith('/') or filepath.startswith('\\'):
                errors.append(f"Invalid path: {filepath}")
                continue

            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code, encoding='utf-8')
            saved.append(str(path))
            print(f"Saved: {path}")
        except Exception as e:
            errors.append(str(e))

    print(f"Saver: {len(saved)} saved, {len(errors)} errors")
    for f in saved:
        print(f"   {f}")

    return {"saved": saved, "count": len(saved), "errors": errors, "success": len(errors) == 0}
