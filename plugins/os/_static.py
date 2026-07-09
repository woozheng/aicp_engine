"""静态文件服务 — 协议 v3.0 系统插件
规则：
  /              → www/index.html
  /admin/        → www/admin/index.html
  /admin         → www/admin/index.html (自动补全)
  /app/style.css → www/app/style.css
  /my_project/   → plugins/my_project/www/index.html
"""
from pathlib import Path
import mimetypes


def _guess_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def _safe_path(file_path: str) -> bool:
    if not file_path:
        return True
    if ".." in file_path:
        return False
    if file_path.startswith("/") or file_path.startswith("\\"):
        return False
    return True


def _try_files(base: Path, file_path: str) -> Path | None:
    """按优先级尝试匹配文件：
    1. 精确路径
    2. 补全 .html
    3. 目录下的 index.html
    """
    # 精确匹配
    exact = base / file_path
    if exact.exists() and exact.is_file():
        return exact
    
    # 补全 .html
    html = base / f"{file_path}.html"
    if html.exists() and html.is_file():
        return html
    
    # 目录下的 index.html
    index = base / file_path / "index.html"
    if index.exists() and index.is_file():
        return index
    
    return None


async def execute(envelop, agent):
    action = envelop.payload.get("action", "serve")
    
    if action == "serve":
        file_path = envelop.payload.get("file_path", "")
        if not file_path:
            file_path = envelop.meta.get("path", "").lstrip("/")
        
        if not file_path:
            file_path = "index.html"
        
        if not _safe_path(file_path):
            envelop.payload = {"error": "Forbidden"}
            return envelop
        
        # 先查项目 www/
        parts = file_path.split("/", 1)
        if len(parts) == 2:
            project, rest = parts
            project_www = Path(f"plugins/{project}/www")
            found = _try_files(project_www, rest)
            if found:
                content = found.read_bytes()
                envelop.meta["static_content"] = content
                envelop.meta["content_type"] = _guess_type(found)
                envelop.payload = {"found": True, "source": f"plugins/{project}/www/"}
                return envelop
        
        # 再查全局 www/
        global_www = Path("www")
        target = global_www / file_path

        # 先尝试直接文件匹配（支持任何类型）
        if target.exists() and target.is_file():
            content = target.read_bytes()
            envelop.meta["static_content"] = content
            envelop.meta["content_type"] = _guess_type(target)
            envelop.payload = {"found": True, "source": "www/"}
            return envelop
            
        global_www = Path("www")
        found = _try_files(global_www, file_path)
        if found:
            content = found.read_bytes()
            envelop.meta["static_content"] = content
            envelop.meta["content_type"] = _guess_type(found)
            envelop.payload = {"found": True, "source": "www/"}
            return envelop
        
        envelop.payload = {"error": "Not found"}
        return envelop
    
    elif action == "list":
        pages = []
        
        plugins_dir = Path("plugins")
        if plugins_dir.exists():
            for project_dir in plugins_dir.iterdir():
                if not project_dir.is_dir() or project_dir.name in ("os", "__pycache__"):
                    continue
                www_dir = project_dir / "www"
                if www_dir.exists():
                    for f in www_dir.rglob("*.html"):
                        rel = str(f.relative_to(www_dir)).replace("\\", "/")
                        pages.append({
                            "path": f"{project_dir.name}/{rel}",
                            "url": f"/{project_dir.name}/{rel}",
                            "size": f.stat().st_size,
                        })
        
        global_www = Path("www")
        if global_www.exists():
            for f in global_www.rglob("*.html"):
                rel = str(f.relative_to(global_www)).replace("\\", "/")
                pages.append({
                    "path": rel,
                    "url": f"/{rel}",
                    "size": f.stat().st_size,
                })
        
        envelop.payload = {"pages": pages}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop