import core
import json
from pathlib import Path

PROJECT = Path(__file__).parent.name

async def execute(envelop, agent):
    payload = envelop.payload
    template_id = payload.get("template_id")

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

    if "name" in payload:
        template["name"] = payload["name"]

    if "labels" in payload:
        template["labels"] = payload["labels"]
        labels = template.get("labels", {})
        all_text = " ".join([
            template.get("name", ""),
            " ".join(labels.get("industry", [])),
            " ".join(labels.get("style", [])),
            " ".join(labels.get("layout", [])),
            " ".join(labels.get("color", [])),
            " ".join(labels.get("component", [])),
            " ".join(labels.get("purpose", []))
        ])
        template["search_text"] = all_text

    if "style_prompt" in payload:
        template["style_prompt"] = payload["style_prompt"]

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)

    envelop.payload = {"success": True, "template_id": template_id, "updated_fields": list(payload.keys())}
    return envelop