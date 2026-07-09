import core
from pathlib import Path

PROJECT = Path(__file__).parent.name

async def execute(envelop, agent):
    payload = envelop.payload
    template_id = payload.get("template_id")

    if not template_id:
        envelop.payload = {"error": "template_id required"}
        return envelop

    data_dir = Path(agent.data_dir) / PROJECT / "memory" / "screenshots"
    file_path = data_dir / f"{template_id}.png"

    if not file_path.exists():
        envelop.payload = {"error": "Image not found"}
        return envelop

    # ✅ 完全照搬个人知识库的写法
    import mimetypes
    envelop.meta["response_type"] = "file"
    envelop.meta["file_path"] = str(file_path)
    envelop.meta["content_type"] = mimetypes.guess_type(file_path.name)[0] or "image/png"
    envelop.meta["file_name"] = file_path.name
    envelop.meta["content_disposition"] = "inline"  # inline 让浏览器直接显示

    envelop.payload = {"ok": True}
    return envelop