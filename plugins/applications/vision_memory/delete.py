import core
from pathlib import Path

PROJECT = Path(__file__).parent.name

async def execute(envelop, agent):
    template_id = envelop.payload.get("template_id")

    if not template_id:
        envelop.payload = {"error": "template_id required"}
        return envelop

    data_dir = Path(agent.data_dir) / PROJECT / "memory"
    json_path = data_dir / "templates" / f"{template_id}.json"
    png_path = data_dir / "screenshots" / f"{template_id}.png"

    deleted = []
    if json_path.exists():
        json_path.unlink()
        deleted.append("json")
    if png_path.exists():
        png_path.unlink()
        deleted.append("png")

    if deleted:
        envelop.payload = {"success": True, "template_id": template_id, "deleted": deleted}
    else:
        envelop.payload = {"success": False, "error": "Template not found"}
    return envelop