"""工作流插件 — 协议 v3.0 系统插件
串行或并行执行多个插件。
"""
import aiohttp
import asyncio


async def execute(envelop, agent):
    action = envelop.payload.get("action", "run")
    
    if action == "run":
        steps = envelop.payload.get("steps", [])
        timeout = envelop.payload.get("timeout", 30)
        
        if not steps:
            envelop.payload = {"error": "No steps defined"}
            return envelop
        
        current_payload = envelop.payload
        current_meta = envelop.meta
        
        async with aiohttp.ClientSession() as session:
            for i, step in enumerate(steps):
                receiver = step if isinstance(step, str) else step.get("receiver", "")
                step_payload = step.get("payload", {}) if isinstance(step, dict) else {}
                
                if not receiver:
                    envelop.payload = {"error": f"Step {i}: no receiver"}
                    return envelop
                
                body = {
                    "payload": {**current_payload, **step_payload},
                    "meta": {**current_meta, "workflow_step": i, "workflow_total": len(steps)},
                }
                
                try:
                    async with session.post(
                        f"{agent.base_url}/api/{receiver}",
                        json=body,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as resp:
                        result = await resp.json()
                        current_payload = result
                except asyncio.TimeoutError:
                    envelop.payload = {"error": f"Step {i} ({receiver}) timeout"}
                    return envelop
                except Exception as e:
                    envelop.payload = {"error": f"Step {i} ({receiver}): {str(e)}"}
                    return envelop
            
            envelop.payload = current_payload
            return envelop
    
    elif action == "parallel":
        steps = envelop.payload.get("steps", [])
        timeout = envelop.payload.get("timeout", 30)
        
        if not steps:
            envelop.payload = {"error": "No steps defined"}
            return envelop
        
        async def run_step(session, step):
            receiver = step if isinstance(step, str) else step.get("receiver", "")
            step_payload = step.get("payload", {}) if isinstance(step, dict) else {}
            
            body = {
                "payload": {**envelop.payload, **step_payload},
                "meta": envelop.meta,
            }
            
            try:
                async with session.post(
                    f"{agent.base_url}/api/{receiver}",
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    result = await resp.json()
                    return {"receiver": receiver, "payload": result}
            except asyncio.TimeoutError:
                return {"receiver": receiver, "error": "timeout"}
            except Exception as e:
                return {"receiver": receiver, "error": str(e)}
        
        async with aiohttp.ClientSession() as session:
            tasks = [run_step(session, s) for s in steps]
            results = await asyncio.gather(*tasks)
        
        envelop.payload = {"results": results}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop