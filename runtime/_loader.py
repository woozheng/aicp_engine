"""插件加载器 + 自动热重载 — 协议 v3.0"""
import importlib.util
import sys
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("aicp")


def _module_name(route: str) -> str:
    return f"plugin_{route.replace('/', '_')}"


def _package_name(route: str) -> str:
    parts = route.split("/")
    if len(parts) > 1:
        return "plugins." + ".".join(parts[:-1])
    return "plugins"


def _calc_route(rel_path: Path, plugin_dir: str) -> str:
    return str(rel_path.with_suffix("")).replace("\\", "/")


def load_all_plugins(plugin_dir: str = "plugins") -> int:
    import core
    root = Path(plugin_dir)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return 0
    loaded = 0
    for py_file in sorted(root.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        if "__pycache__" in py_file.parts:
            continue
        if "_cache" in py_file.parts:
            continue
        rel = py_file.relative_to(root)
        route = _calc_route(rel, plugin_dir)
        if route in core.plugins:
            continue
        if _load_single(py_file, route, root):
            loaded += 1
    return loaded


def _load_single(py_file: Path, route: str, plugin_dir: Path) -> bool:
    import core
    module_name = _module_name(route)
    package_name = _package_name(route)
    try:
        _ensure_parent_packages(route)
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        module = importlib.util.module_from_spec(spec)
        module.__package__ = package_name
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if hasattr(module, "execute"):
            core.plugins[route] = module.execute
            return True
    except Exception as e:
        logger.warning(f"Failed to load {route}: {e}")
    return False


def _reload_single(py_file: Path, route: str) -> bool:
    import core
    module_name = _module_name(route)
    package_name = _package_name(route)
    if module_name in sys.modules:
        del sys.modules[module_name]
    try:
        _ensure_parent_packages(route)
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        module = importlib.util.module_from_spec(spec)
        module.__package__ = package_name
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if hasattr(module, "execute"):
            core.plugins[route] = module.execute
            return True
    except Exception as e:
        logger.warning(f"Hot reload failed for {route}: {e}")
    return False


def _ensure_parent_packages(route: str):
    parts = route.split("/")
    if len(parts) <= 1:
        return
    for i in range(1, len(parts)):
        pkg_path = "plugins." + ".".join(parts[:i])
        pkg_name = "plugin_" + "_".join(parts[:i])
        if pkg_name not in sys.modules:
            pkg = type(sys)(pkg_name)
            pkg.__package__ = pkg_path
            pkg.__path__ = [str(Path("plugins") / "/".join(parts[:i]))]
            sys.modules[pkg_name] = pkg


async def start_hot_reload_watcher(plugin_dir: str = "plugins", interval: float = 2.0):
    root = Path(plugin_dir)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    _mtime_cache: dict[str, float] = {}
    for py_file in root.rglob("*.py"):
        if py_file.name == "__init__.py" or "__pycache__" in py_file.parts:
            continue
        if "_cache" in py_file.parts:
            continue
        _mtime_cache[str(py_file)] = py_file.stat().st_mtime
    logger.info(f"Hot reload watcher started (interval={interval}s, watching {plugin_dir}/)")
    while True:
        await asyncio.sleep(interval)
        try:
            current_files = set()
            for py_file in root.rglob("*.py"):
                if py_file.name == "__init__.py" or "__pycache__" in py_file.parts:
                    continue
                if "_cache" in py_file.parts:
                    continue
                filepath = str(py_file)
                current_files.add(filepath)
                current_mtime = py_file.stat().st_mtime
                if filepath not in _mtime_cache:
                    rel = py_file.relative_to(root)
                    route = _calc_route(rel, plugin_dir)
                    await asyncio.sleep(0.5)
                    if py_file.stat().st_mtime == current_mtime:
                        if _load_single(py_file, route, root):
                            _mtime_cache[filepath] = current_mtime
                            logger.info(f"New plugin: {route}")
                elif current_mtime > _mtime_cache[filepath]:
                    rel = py_file.relative_to(root)
                    route = _calc_route(rel, plugin_dir)
                    if _reload_single(py_file, route):
                        _mtime_cache[filepath] = current_mtime
                        logger.info(f"Hot reloaded: {route}")
            removed = set(_mtime_cache.keys()) - current_files
            for filepath in removed:
                import core
                py_file = Path(filepath)
                rel = py_file.relative_to(root)
                route = _calc_route(rel, plugin_dir)
                del _mtime_cache[filepath]
                if route in core.plugins:
                    del core.plugins[route]
                    logger.info(f"Removed plugin: {route}")
        except Exception as e:
            logger.debug(f"Watcher error: {e}")