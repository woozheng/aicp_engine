# plugins/builtins/aicp/chat.py
"""AICP Chat API — 远端 chatEnvelop 端点"""

from core import Envelop
from runtime._aicp_llm import AICP_LLM


async def execute(envelop, agent):
    # print(agent.aicp_llm._system_prompt[:200])
    messages = envelop.payload.get("messages", [])
    print(messages)
    model = envelop.payload.get("model")
    temperature = envelop.payload.get("temperature")
    max_iter = envelop.payload.get("max_iter", 20)
    
    if not hasattr(agent, 'aicp_llm'):
        agent.aicp_llm = AICP_LLM(agent.config)
    
    result = await agent.aicp_llm.chatEnvelop(
        messages,
        model=model,
        temperature=temperature,
        max_iter=max_iter
    )
    print(f"[LOCAL] result.payload: {result.payload}")
    payload = result.payload if hasattr(result, 'payload') else result
    return Envelop(
        receiver=envelop.sender,
        payload={
            "ok": payload.get("ok", True),
            "data": payload.get("data", ""),
            "error": payload.get("error", "")
        }
    )