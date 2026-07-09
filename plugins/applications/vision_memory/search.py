import core
import json
from pathlib import Path
from difflib import SequenceMatcher

PROJECT = Path(__file__).parent.name

def calc_match_score(query: str, template: dict) -> int:
    query_lower = query.lower()
    query_words = set(query_lower.split())
    score = 0

    search_text = template.get("search_text", "").lower()
    search_words = set(search_text.split())
    match_count = len(query_words & search_words)
    score += match_count * 10

    labels = template.get("labels", {})
    all_label_values = []
    for vals in labels.values():
        if isinstance(vals, list):
            all_label_values.extend([v.lower() for v in vals])
        elif isinstance(vals, str):
            all_label_values.append(vals.lower())

    for word in query_words:
        if word in all_label_values:
            score += 15
        for label in all_label_values:
            if len(word) > 2 and (word in label or label in word):
                score += 5

    name = template.get("name", "").lower()
    if query_lower in name or name in query_lower:
        score += 20

    similarity = SequenceMatcher(None, query_lower, search_text).ratio()
    score += similarity * 10

    return score


async def execute(envelop, agent):
    payload = envelop.payload
    action = payload.get("action", "search")

    if action == "search":
        query = payload.get("query", "")
        limit = payload.get("limit", 5)

        if not query:
            envelop.payload = {"error": "query required"}
            return envelop

        data_dir = Path(agent.data_dir) / PROJECT / "memory" / "templates"
        if not data_dir.exists():
            envelop.payload = {
                "query": query,
                "results": [],
                "total": 0,
                "message": "模板库为空，请先上传截图"
            }
            return envelop

        templates = []
        for json_file in data_dir.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    templates.append(json.load(f))
            except:
                continue

        if not templates:
            envelop.payload = {
                "query": query,
                "results": [],
                "total": 0,
                "message": "模板库为空，请先上传截图"
            }
            return envelop

        scored = []
        for t in templates:
            score = calc_match_score(query, t)
            if score > 0:
                scored.append((t, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = [t for t, s in scored[:limit]]

        match_summary = []
        for t, s in scored[:limit]:
            match_summary.append(f"{t.get('name')} (匹配度: {s}分)")

        envelop.payload = {
            "query": query,
            "results": results,
            "total": len(results),
            "match_summary": match_summary,
            "template_count": len(templates),
            "mode": "轻量检索（标签+关键词）",
            "hint": f"当前模板库共{len(templates)}个" if len(templates) < 30 else "模板库较丰富，可考虑升级到向量检索"
        }
        return envelop

    elif action == "list":
        data_dir = Path(agent.data_dir) / PROJECT / "memory" / "templates"
        templates = []
        if data_dir.exists():
            for json_file in data_dir.glob("*.json"):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        t = json.load(f)
                        templates.append({
                            "id": t.get("id"),
                            "name": t.get("name"),
                            "labels": t.get("labels", {}),
                            "style_prompt": t.get("style_prompt", ""),
                            "summary": {
                                "colors": t.get("summary", {}).get("colors", {}),
                                "layout": t.get("summary", {}).get("layout", {})
                            },
                            "created_at": t.get("created_at")
                        })
                except:
                    continue
        templates.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        envelop.payload = {"templates": templates, "total": len(templates)}
        return envelop

    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop