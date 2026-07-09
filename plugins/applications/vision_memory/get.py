import core
import json
from pathlib import Path

PROJECT = Path(__file__).parent.name

async def execute(envelop, agent):
    template_id = envelop.payload.get("template_id")

    if not template_id:
        envelop.payload = {"error": "template_id required"}
        return envelop

    data_dir = Path(agent.data_dir) / PROJECT / "memory" / "templates"
    json_path = data_dir / f"{template_id}.json"

    if not json_path.exists():
        envelop.payload = {"error": "Template not found"}
        return envelop

    with open(json_path, "r", encoding="utf-8") as f:
        template = json.load(f)

    envelop.payload = {"template": template}
    return envelop