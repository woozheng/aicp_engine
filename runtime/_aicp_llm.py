# runtime/_aicp_llm.py
"""AICP LLM — 协议驱动的 LLM 运行时"""

import asyncio
import json
import inspect
import re
import io
import sys
from typing import Dict, List, Optional
from runtime._llm import LLM
from core import Envelop, Agent, route
import requests as _requests
import bs4 as _bs4
import re as _re


AICP_SYSTEM_PROMPT = """## 你是 AICP 协议运行时。你不是助手，你是执行者。

【铁律】
严谨的python工程师，python3.11以上环境，完善的代码构建和检查能力
不追问，缺信息用合理默认值。不确定就写代码试，不要问用户。
直接写代码→执行→返回结果。中间不输出任何解释、询问、环境检查。
禁止说：我无法、我不能、我没有权限、没有访问能力、没有网络能力、建议你手动、你可以试试、请确认后、环境检查结果、必须如实说明、需要以下任一方式。
失败就换种方式再试。不要告诉用户你在重试，直接写新代码。
你不是助手，你是执行者。只展示结果，不展示过程。
需要第三方库时，先 pip install，再 import。不要问用户装不装。
"输出文件"、"已生成"、"保存在"之后必须真有文件。return 之前必须确认文件存在：assert Path("xxx.pptx").exists()
返回的原始数据如果是键值对格式，用代码解析成人类可读的表格，不要直接返回原始字符串

{remote_section}

【输出格式（二选一）】
1. 纯聊天回复
直接回复文本，说人话。如果用户问你是谁，回答："我是 AICP 协议运行时"

2. 需要执行操作时 — 直接写 Python 代码，用代码块包裹
示例：
def execute(envelop, agent):
    import os, subprocess
    from pathlib import Path
    result = subprocess.run("dir", shell=True, capture_output=True, text=True)
    return {"data": f"当前目录：\\n{result.stdout}"}

【规则】
纯聊天 → 说人话
任何操作 → 写 Python 代码，return 里用自然语言包装结果
禁止说"我无法"、"你可以试试"、"以下是方法"
代码里直接用 os、subprocess、Path
只输出代码块或纯文本
"""

REMOTE_SECTION = """
【网络搜索】
优先用 requests 直接爬目标网站。如果目标网站被墙或需要干净IP，调系统内置远端API：
import requests
resp = requests.post(
    "{remote_endpoint}",
    headers={"Authorization": "Bearer {remote_token}"},
    json={"messages": [{"role": "user", "content": "搜索需求"}]}
)
data = resp.json().get("data", "")
拿到 data 后用 Python 解析、清洗、格式化，不要直接展示原始 JSON
"""


class AICP_LLM:
    
    def __init__(self, config: dict, is_remote: bool = False):
        self.llm = LLM(config)
        self._agent = None
        self._is_remote = is_remote
        
        if is_remote:
            self._system_prompt = AICP_SYSTEM_PROMPT.replace("{remote_section}", "")
        else:
            remote = config.get("remote_aicp", {})
            endpoint = remote.get("endpoint", "")
            token = remote.get("token", "")
            if endpoint:
                section = REMOTE_SECTION.replace("{remote_endpoint}", endpoint).replace("{remote_token}", token)
            else:
                section = ""
            self._system_prompt = AICP_SYSTEM_PROMPT.replace("{remote_section}", section)
    
    @property
    def agent(self):
        if self._agent is None:
            self._agent = Agent(
                llm=self.llm,
                chatEnvelop=self.chatEnvelop
            )
        return self._agent
    
    async def chatEnvelop(
        self,
        messages: List[Dict],
        model: str = None,
        role: str = None,
        temperature: float = None,
        max_tokens: int = None,
        stream: bool = False,
        max_iter: int = 5,
        **kwargs
    ) -> Envelop:
        
        full_messages = [
            {"role": "system", "content": self._system_prompt},
            *messages
        ] if self._system_prompt else messages
        
        for i in range(max_iter):
            raw = await self.llm.chat(
                full_messages,
                model=model,
                role=role,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            protocol = self._parse_envelop(raw)
            if protocol:
                try:
                    result = await route(protocol, self.agent)
                    return Envelop(receiver="user", payload={"ok": True, "data": result.payload})
                except Exception as e:
                    full_messages.append({"role": "assistant", "content": raw})
                    full_messages.append({"role": "system", "content": f"执行失败: {str(e)}，请直接写代码完成"})
                    continue
            
            code = self._extract_code(raw)
            if code:
                result = await self._exec(code)
                if result.get("ok"):
                    return Envelop(receiver="user", payload=result)
                
                hint = "已失败多次，请换一种方式。" if i >= 2 else ""
                error_msg = f"代码执行失败: {result.get('error')}\n\n源码:\n{code[:500]}\n\n{hint}"
                print(f"[ERROR] {error_msg}")
                full_messages.append({"role": "assistant", "content": raw})
                full_messages.append({"role": "system", "content": error_msg})
                continue
            
            return Envelop(receiver="user", payload={"ok": True, "data": raw})
        
        return Envelop(receiver="user", payload={"ok": False, "error": "达到最大迭代次数"})
    
    def _extract_code(self, raw: str) -> Optional[str]:
        match = re.search(r'```python\s*\n(.*?)```', raw, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r'```\s*\n(.*?)```', raw, re.DOTALL)
        if match:
            code = match.group(1).strip()
            if 'def execute' in code or 'import ' in code or 'agent.' in code:
                return code
        return None
    
    def _parse_envelop(self, raw: str) -> Optional[Envelop]:
        try:
            data = json.loads(raw.strip())
            if isinstance(data, dict) and "receiver" in data and "payload" in data:
                return Envelop.from_dict(data)
        except json.JSONDecodeError:
            pass
        match = re.search(r'```json\s*\n(.*?)```', raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1).strip())
                if isinstance(data, dict) and "receiver" in data and "payload" in data:
                    return Envelop.from_dict(data)
            except json.JSONDecodeError:
                pass
        return None
    
    async def _exec(self, code: str) -> dict:
        import os as _os
        import subprocess as _subprocess
        from pathlib import Path as _Path
        
        def _auto_run(cmd, timeout=30):
            import platform
            if platform.system() == "Windows" and isinstance(cmd, list):
                cmd = " ".join(cmd)
            result = _subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
            for enc in ["utf-8", "gbk", "gb2312", "gb18030", "latin-1"]:
                try:
                    stdout = result.stdout.decode(enc)
                    if stdout and len(stdout.strip()) > 0:
                        return stdout
                except:
                    continue
            return result.stdout.decode("utf-8", errors="replace")
        
        def _auto_get(url, timeout=15):
            resp = _requests.get(url, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if resp.encoding and resp.encoding.lower() != "iso-8859-1":
                resp.encoding = resp.encoding
            else:
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        
        local_vars = {}
        try:
            exec(code, {
                "agent": self.agent,
                "asyncio": asyncio,
                "json": json,
                "Envelop": Envelop,
                "os": _os,
                "subprocess": _subprocess,
                "Path": _Path,
                "open": open,
                "requests": _requests,
                "BeautifulSoup": _bs4.BeautifulSoup,
                "re": _re,
                "run": _auto_run,
                "fetch": _auto_get,
            }, local_vars)
            
            execute_fn = local_vars.get("execute")
            if execute_fn and callable(execute_fn):
                if inspect.iscoroutinefunction(execute_fn):
                    result = await execute_fn(Envelop(payload={"state": {}}), self.agent)
                else:
                    result = execute_fn(Envelop(payload={"state": {}}), self.agent)
                if isinstance(result, dict):
                    data = result.get("data", result)
                else:
                    data = str(result)
                return {"ok": True, "data": data}
            
            return {"ok": True, "data": "代码执行完成，无返回值"}
            
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)}"}