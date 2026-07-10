
# AICP Engine
[中文文档](README_CN.md) | [English](README.md)
> 基于AICP协议开发的AI自生长OS引擎,AI的生产力平台.
> Base on [Agent Interaction & Communication Protocol](https://github.com/woozheng/aicp)
> 不需要任何框架！不需下载任何工具，人类不需要学习任何配置调度。AI自调度，自编排。
> 支持云端部署，远程访问。
> 人类定义协议,定义需求， AI负责生长。AICP Engin 不生产插件，只解决需求。
> 人类只负责验收结果。不是看编排有多花哨，流程有多绚丽，框架有多华丽
> 新一代 AI AGI 范式，信任AI，协议约束AI，连接一切，跨越语言，平台，其他顺其自然。

**AICP Engine 不仅仅是开发应用的平台，也是开发AI Agent的开发平台。**

---

## 📖 核心功能

### 三层需求实现

1、瑞士军刀：[AICP_Cil](https://github.com/woozheng/aicp_cli) ，项目内置，无需启动引擎，一句话完成即时需求。
2、自动步枪：引擎主首页主控台，对话式AI，自带记忆，任务、调度、支持邮件/飞书/微信 等多通道任务请求。
3、超级大炮：AICP Studio，协议驱动的AI应用开发平台，AI全部自行生成并部署任何你想实现的web应用，网站、工具，可构建实现各类需求。


内置  Vision Agent，视觉化自动识别加自动意图操作(windows desktop)
以上agent以及studio开发工具，均由AI由协议驱动根据需求在平台创建。

**平台的核心特征不在于强大的agent，在于人类的想象力，因为这是一个协议让AI自举的平台引擎**


### 📸 Studio 项目展示

#### 1. 任意文档、网页链接转 MD
讨论需求 5 分钟，AI 生成 30 秒，调试 5 分钟，Bug：0

![Office to MD](docs/image/officetomd.png)

#### 2. AI 网站素材视觉知识库
分析流行网页视觉特征，输出模板 JSON，Studio 可仿照生成。需求 5 分钟，AI 生成 30 秒，调试 3 分钟，Bug：0

![AI Web Vision](docs/image/AIweb.png)

#### 3. Raft 分布式算法可视化
需求 1 分钟，生成 30 秒，一次过，Bug：0

![Raft Visualization](docs/image/raft.png)

#### 4. 物流调度模拟系统
100 辆卡车，500 个订单，完整状态分配，实时位置上报。需求 3 分钟，生成 30 秒，一把过，Bug：0

![Logistics Simulation](docs/image/logiit.png)

---

**所有项目均由 AICP Studio 生成，AI 完成全部编码与部署。人类只定义需求验收结果。**

## 快速安装

### Windows（桌面版，含 GUI）

```bash
git clone https://github.com/woozheng/aicp_engine.git
cd aicp-engine
pip install -r requirements.txt
copy aicp.yaml.example aicp.yaml  # 填入你的 API Key
python -m runtime
```
系统出现托盘，右键菜单打开应用（所有任务均由Studio生成）
内置桌面应用 MK助手：自动划词、中键启动魔法鼠标可框选实现视觉识别，自动操作。
内置桌面应用，AI LLM Newbula引擎，可将页面AI转化为 web llm provider，兼容openai接口层调用
内置网页应用，office md 五件套（乱文本转MD、项目分析转MD、文字处理、任意office文档转MD、思维导图）
内置网页应用，Studio 开发 windows 桌面版 / 网页版


### Docker（服务器 / macOS / Linux）

From [Releases](https://github.com/woozheng/aicp_engine/releases) download `aicp-server.tar`,then


```bash
docker load -i aicp-server.tar
docker run --rm -v $(pwd):/out aicp-server cp /app/aicp.yaml.example /out/aicp.yaml
vim aicp.yaml  # 填入 API Key
docker run -d -p 9000:9000 -p 9001:9001 -p 9002:9002 -v $(pwd)/aicp.yaml:/app/aicp.yaml --name aicp-server aicp-server

```
浏览器打开 http://127.0.0.1:9000。
网页集成 windows 平台所有应用网页版。（不包含桌面应用）

---

## 📂 studio开发指南
```text
1、建立项目
2、发送协议 （网页版AI即可，deepseek、千问coding、豆包coding、kimi，均可以，阅读协议自动生成前后端代码，无需燃烧Token）
3、确认需求  
4、ai生成，自动落盘(windows桌面版)/网页书签落盘(mac/linux)，自动部署
5、预览结果
6、提出修正，直到AI自动修正完毕。
```

 无需关注ai代码，代码是生成应用时的附加产物，就像编译的二进制文件，谁关注它呢，人类只关注结果！

---
## License

[MIT](LICENSE)

## 🤝 贡献

欢迎通过 Issue / PR 提交新插件、新通道、新 Agent 角色。任何能被封装为 `async def execute(envelop, agent)` 的函数都可注册到 `core.plugins` 成为路由目标。

**协议即神经 —— 让每一个 Agent、工具、通道都成为协议网络上的一个节点。**
