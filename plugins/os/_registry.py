"""插件发现 — 协议 v3.0 系统插件
提供 /api/list 和热重载能力。
"""
import core
import json


async def execute(envelop, agent):
    action = envelop.payload.get("action", "list")
    
    if action == "list":
        apis = []
        for name in core.plugins:
            api_info = {"route": f"/api/{name}"}
            try:
                mod_name = f"plugins.{name.replace('/', '.')}"
                mod = __import__(mod_name, fromlist=["help"])
                if hasattr(mod, "help"):
                    help_data = mod.help()
                    try:
                        json.dumps(help_data)
                        api_info["help"] = help_data
                    except (TypeError, ValueError):
                        api_info["help"] = str(help_data)
            except Exception:
                pass
            apis.append(api_info)
        
        envelop.payload = {"apis": apis, "total": len(apis)}
        return envelop
    
    elif action == "reload":
        route_name = envelop.payload.get("route", "")
        if not route_name:
            envelop.payload = {"error": "No route specified"}
            return envelop
        
        import importlib.util
        import sys
        from pathlib import Path
        
        plugin_file = Path(f"plugins/{route_name}.py")
        if not plugin_file.exists():
            envelop.payload = {"error": f"Plugin not found: {route_name}"}
            return envelop
        
        module_name = f"hot_{route_name.replace('/', '_')}"
        if module_name in sys.modules:
            del sys.modules[module_name]
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            if hasattr(module, "execute"):
                core.plugins[route_name] = module.execute
                agent.log.info(f"Hot reloaded: {route_name}")
                envelop.payload = {"ok": True, "route": route_name}
            else:
                envelop.payload = {"error": "No execute function"}
        except Exception as e:
            envelop.payload = {"error": f"Reload failed: {str(e)}"}
        
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop