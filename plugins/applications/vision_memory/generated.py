import core
from pathlib import Path

PROJECT = Path(__file__).parent.name

async def execute(envelop, agent):
    """获取预生成的 HTML 模板内容"""
    payload = envelop.payload
    template_id = payload.get("template_id")

    if not template_id:
        envelop.payload = {"error": "template_id required"}
        return envelop

    data_dir = Path(agent.data_dir) / PROJECT / "memory" / "generated"
    html_path = data_dir / f"{template_id}.html"

    if not html_path.exists():
        envelop.payload = {"error": "HTML file not found"}
        return envelop

    # 直接返回 HTML 内容
    html_content = html_path.read_text(encoding="utf-8")
    envelop.payload = {"content": html_content, "template_id": template_id}
    return envelop