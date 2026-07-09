import core
import json
import base64
import shutil
import hashlib
import re
from pathlib import Path
from datetime import datetime

PROJECT = Path(__file__).parent.name

def generate_html_from_template(analysis, template_id, name):
    """从解析数据生成完整 HTML 页面（基于外部模板文件）"""
    
    # 读取模板文件
    template_path = Path(__file__).parent / "template.html"
    if not template_path.exists():
        # fallback：如果模板文件不存在，返回简单的 HTML
        return f"<html><body><h1>{name}</h1><p>模板文件缺失，请检查 template.html</p></body></html>"
    
    html_template = template_path.read_text(encoding="utf-8")
    
    # 提取数据（带默认值）
    colors = analysis.get("colors", {})
    typography = analysis.get("typography", {})
    spacing = analysis.get("spacing", {})
    layout = analysis.get("layout", {})
    components = analysis.get("components", [])
    
    # 检测布局特征
    has_sidebar = any("sidebar" in c.get("type", "").lower() or "侧边" in c.get("type", "") for c in components)
    has_hero = any("hero" in c.get("type", "").lower() for c in components)
    
    # 构建导航
    nav_items = ["产品", "解决方案", "资源", "定价"]
    nav_links = "\n".join([f'<a href="#" class="nav-link">{item}</a>' for item in nav_items])
    
    # 构建侧边栏
    if has_sidebar:
        sidebar_items = ["仪表盘", "项目", "任务", "设置"]
        sidebar_html = '<ul style="list-style:none;padding:0;">\n' + "\n".join([
            f'<li class="sidebar-item">{item}</li>' for item in sidebar_items
        ]) + '\n</ul>'
    else:
        sidebar_html = ""
    
    # 构建 Hero
    if has_hero:
        hero_html = '''
        <section class="hero">
            <h1 class="hero-title">构建下一代产品</h1>
            <p class="hero-desc">用 AI 驱动的设计系统，快速生成高质量的网页应用</p>
            <button class="btn-primary">开始使用 →</button>
        </section>
        '''
    else:
        hero_html = ""
    
    # 构建卡片
    cards_html = "\n".join([
        f'''
        <div class="card">
            <div class="card-icon">📄</div>
            <h3 class="card-title">功能模块 {i+1}</h3>
            <p class="card-desc">这里填写功能描述，让用户了解这个模块的作用。</p>
        </div>
        ''' for i in range(3)
    ])
    
    # 变量替换
    replacements = {
        "{{NAME}}": name,
        "{{PRIMARY}}": colors.get("primary", "#6366f1"),
        "{{SECONDARY}}": colors.get("secondary", "#0A2540"),
        "{{BACKGROUND}}": colors.get("background", "#FFFFFF"),
        "{{TEXT_HEADING}}": colors.get("text_heading", "#1e293b"),
        "{{TEXT_BODY}}": colors.get("text_body", "#475569"),
        "{{BORDER}}": colors.get("border", "#e2e8f0"),
        "{{RADIUS_MEDIUM}}": spacing.get("border_radius", {}).get("medium", "12px"),
        "{{RADIUS_LARGE}}": spacing.get("border_radius", {}).get("large", "20px"),
        "{{RADIUS_PILL}}": spacing.get("border_radius", {}).get("pill", "9999px"),
        "{{COMPONENT_GAP}}": spacing.get("component_gap", "24px"),
        "{{SECTION_PADDING}}": spacing.get("section_padding", "80px 120px"),
        "{{MAX_WIDTH}}": layout.get("max_width", "1200px"),
        "{{HEADING_FONT}}": typography.get("heading", {}).get("font_family", "Inter, sans-serif"),
        "{{BODY_FONT}}": typography.get("body", {}).get("font_family", "Inter, sans-serif"),
        "{{HEADING_SIZE}}": typography.get("heading", {}).get("size", "48px"),
        "{{HEADING_WEIGHT}}": typography.get("heading", {}).get("weight", "700"),
        "{{HEADING_LINE}}": typography.get("heading", {}).get("line_height", "1.2"),
        "{{BODY_SIZE}}": typography.get("body", {}).get("size", "16px"),
        "{{BODY_WEIGHT}}": typography.get("body", {}).get("weight", "400"),
        "{{BODY_LINE}}": typography.get("body", {}).get("line_height", "1.6"),
        "{{NAV_LINKS}}": nav_links,
        "{{SIDEBAR}}": sidebar_html,
        "{{HERO}}": hero_html,
        "{{CARDS}}": cards_html,
    }
    
    for key, value in replacements.items():
        html_template = html_template.replace(key, str(value))
    
    return html_template


async def execute(envelop, agent):
    payload = envelop.payload
    action = payload.get("action")

    if action == "ingest_from_file":
        file_path = payload.get("file_path")
        name = payload.get("name", "未命名模板")
        tags_override = payload.get("tags", [])

        if not file_path:
            envelop.payload = {"error": "file_path required"}
            return envelop

        if not agent.llm:
            envelop.payload = {"error": "LLM not available"}
            return envelop

        # 准备目录
        data_dir = Path(agent.data_dir) / PROJECT / "memory"
        screenshots_dir = data_dir / "screenshots"
        templates_dir = data_dir / "templates"
        generated_dir = data_dir / "generated"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        templates_dir.mkdir(parents=True, exist_ok=True)
        generated_dir.mkdir(parents=True, exist_ok=True)

        # 生成 ID 并保存截图
        file_hash = hashlib.md5(open(file_path, "rb").read()).hexdigest()[:8]
        template_id = f"tpl_{datetime.now().strftime('%Y%m%d')}_{file_hash}"
        target_path = screenshots_dir / f"{template_id}.png"
        shutil.copy(file_path, target_path)

        # 图片转 base64
        with open(target_path, "rb") as f:
            img_base64 = base64.b64encode(f.read()).decode()

        # ============================================================
        # 像素级量化解析
        # ============================================================
        analysis = await agent.llm.chat_json([
            {
                "role": "system",
                "content": """你是一个**像素级UI设计分析专家**。你的任务是从截图中提取**精确、可量化的设计数据**，直接用于代码生成。

## 输出要求

1. **所有数值必须精确**（px/rem/em/%），禁止使用"大号""中等""较宽"等描述性词汇
2. **如果无法100%确定，给出合理估算值并用 ~ 前缀标注**（如 "~24px"）
3. **颜色必须是 HEX 或 rgba 格式**
4. **字体必须包含备用字体栈**

## 输出 JSON 结构

{
  "layout": {
    "type": "flex" | "grid" | "block",
    "direction": "row" | "column" | "row-reverse" | "column-reverse",
    "justify": "flex-start" | "center" | "flex-end" | "space-between" | "space-around",
    "align": "flex-start" | "center" | "flex-end" | "stretch",
    "gap": "精确值",
    "padding": "精确值（如 80px 120px）",
    "max_width": "精确值",
    "margin": "精确值（如 0 auto）"
  },
  "colors": {
    "primary": "#HEX",
    "secondary": "#HEX",
    "background": "#HEX",
    "text_heading": "#HEX",
    "text_body": "#HEX",
    "border": "#HEX",
    "accent": "#HEX"
  },
  "typography": {
    "heading": {
      "font_family": "字体名, fallback, sans-serif",
      "size": "精确px值",
      "line_height": "数字（如 1.2）",
      "weight": "100-900 或 normal/bold",
      "letter_spacing": "精确值（如 -0.02em）",
      "text_transform": "none" | "uppercase" | "lowercase" | "capitalize"
    },
    "body": {
      "font_family": "字体名, fallback, sans-serif",
      "size": "精确px值",
      "line_height": "数字",
      "weight": "数字",
      "letter_spacing": "精确值"
    },
    "small": {
      "size": "精确px值",
      "weight": "数字",
      "color": "#HEX"
    }
  },
  "spacing": {
    "section_padding": "精确值（上下 左右）",
    "component_gap": "精确值",
    "item_padding": "精确值",
    "card_padding": "精确值",
    "border_radius": {
      "small": "精确值",
      "medium": "精确值",
      "large": "精确值",
      "pill": "精确值或 9999px"
    }
  },
  "components": [
    {
      "type": "组件类型（如 button / card / input / nav / hero）",
      "position": "位置描述（如 顶部导航 / 左列 / Hero区）",
      "style": {
        "display": "flex / grid / block",
        "padding": "精确值",
        "margin": "精确值",
        "background": "色值或渐变",
        "color": "色值",
        "border": "精确值 solid 色值",
        "border_radius": "精确值",
        "box_shadow": "完整 box-shadow 值",
        "font_size": "精确值",
        "font_weight": "数字",
        "width": "精确值或 %",
        "height": "精确值或 auto",
        "gap": "精确值（flex/grid 子元素间距）"
      },
      "children": [
        {
          "type": "子组件类型",
          "content": "文字内容示例",
          "style": { ... }
        }
      ]
    }
  ],
  "breakpoints": {
    "mobile": { "max_width": "768px", "changes": "布局变化简述" },
    "tablet": { "max_width": "1024px", "changes": "布局变化简述" }
  },
  "style_prompt": "一句话描述整体视觉风格，用于 AI 生成类似图片",
  "labels": {
    "industry": ["行业1", "行业2"],
    "style": ["风格1", "风格2"],
    "layout": ["布局特征1", "特征2"],
    "color": ["色彩感知1", "感知2"],
    "component": ["组件1", "组件2"],
    "purpose": ["用途1", "用途2"]
  }
}

## 估算规则

- **间距**：如果无法确定精确值，按常见设计系统估算（4/8/12/16/20/24/32/40/48/64/80/120）
- **字体大小**：按常见比例估算（12/14/16/18/20/24/32/40/48/56/64）
- **颜色**：用取色器思维给出最接近的 HEX
"""
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}},
                    {"type": "text", "text": "请精确分析这张截图，输出完整的量化 JSON。注意：所有数值必须精确或合理估算。"}
                ]
            }
        ])

        # ============================================================
        # 组装模板 JSON
        # ============================================================
        labels = analysis.get("labels", {})
        if tags_override:
            labels["custom"] = tags_override

        all_text = " ".join([
            name,
            " ".join(labels.get("industry", [])),
            " ".join(labels.get("style", [])),
            " ".join(labels.get("layout", [])),
            " ".join(labels.get("color", [])),
            " ".join(labels.get("component", [])),
            " ".join(labels.get("purpose", []))
        ])

        template = {
            "id": template_id,
            "name": name,
            "source": {
                "type": "screenshot",
                "file": f"screenshots/{template_id}.png",
                "uploaded_at": datetime.now().isoformat()
            },
            "labels": labels,
            "search_text": all_text,
            "summary": {
                "layout": analysis.get("layout", {}),
                "colors": analysis.get("colors", {}),
                "typography": analysis.get("typography", {}),
                "spacing": analysis.get("spacing", {}),
                "components": analysis.get("components", []),
                "breakpoints": analysis.get("breakpoints", {})
            },
            "style_prompt": analysis.get("style_prompt", ""),
            "created_at": datetime.now().isoformat()
        }

        # 保存 JSON
        json_path = templates_dir / f"{template_id}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2, ensure_ascii=False)

        # ============================================================
        # 🔥 预生成 HTML
        # ============================================================
        html_content = generate_html_from_template(analysis, template_id, name)
        
        # 按 ID 保存
        html_path_id = generated_dir / f"{template_id}.html"
        html_path_id.write_text(html_content, encoding="utf-8")
        
        # 按名称保存（方便用户查找，覆盖同名）
        safe_name = re.sub(r'[^\w\-_\u4e00-\u9fa5]', '_', name)
        html_path_name = generated_dir / f"{safe_name}.html"
        html_path_name.write_text(html_content, encoding="utf-8")
        
        agent.log.info(f"[ingest] 预生成 HTML: {html_path_name}")

        envelop.payload = {
            "success": True,
            "template_id": template_id,
            "name": name,
            "summary": {
                "layout": analysis.get("layout", {}),
                "colors": analysis.get("colors", {}),
                "style_prompt": analysis.get("style_prompt", "")
            },
            "html_path": str(html_path_name),
            "message": f"已成功入库：{name}（含预生成 HTML）"
        }
        return envelop

    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop 