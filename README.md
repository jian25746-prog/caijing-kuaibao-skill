# 财经快报 AI 制作流水线（Claude Code Skills）

**An AI newsroom pipeline for Claude Code that turns verified daily financial news into publish-ready short videos.**

From overnight news scanning to fact-checked scripts, rendered vertical cards, 12-second B-roll shorts and multi-platform publishing (YouTube, WeChat Channels, Toutiao) — built and battle-tested on a real daily finance show. Every fact and number in the output must trace back to the original source; anything unverified blocks rendering. Documentation is in Chinese.

一套每天自动产出**财经短视频快报**的 Claude Code skill：从凌晨抓取国内外财经新闻开始，经过选题、逐条事实核查、写旁白与切片文案、发布前硬门禁校验，到渲染竖屏卡片、合成 12 秒成片，最后分发到 YouTube / 微信视频号 / 今日头条，并为小红书生成手动发布包。

作者用它每天运营一档真实节目《7点财经快报》，本仓库的规则、阈值和"血泪教训"注释都来自几个月的实跑。

```
 caijing-kuaibao              caijing-broll-compose         caijing-distribute
 ─────────────────            ─────────────────────         ──────────────────
 抓取标题 → 选题分排序          PNG卡片 + 素材库B-roll          解析当日产物 → 写各平台文案
 → 防伪核查(事实白名单)    →    → 12秒竖屏成片            →    → 抽封面 → 定时发布
 → 旁白脚本 → 切片文案          (全天统一选片/跨天冷却)          → 播放数据回流给次日选题
 → 发布硬门禁 → PNG渲染
```

## 它和"让 AI 写新闻"有什么不同

- **事实只从原文来**：选题阶段只看标题，不让模型写摘要；最终入选的每条新闻由核查步骤回到原文**机械提取**事实片段，产出 `verified_facts`（可写事实）、`allowed_numbers`（可用数字）和 `forbidden_facts`（被原文推翻、禁止使用的说法）。写稿只准用白名单里的东西。
- **发布硬门禁**：`validate_slices.py` 在渲染前逐条检查身份对齐、数字是否在白名单、是否残留占位符等，不通过就停止，不"带病出片"。
- **来源分级**：A 级媒体、B 级聚合/快讯源、C 级付费墙线索分开处理；B 级快讯必须有独立来源印证才能发。
- **选题用相对排序**：三维"选题分"（切身度 / 反差度 / 信息差）排序 + 类别配额保多样性，热度只做同分裁决，不再有"热词永远霸榜"的问题。
- **合规规则内置**：`prompts/rules/compliance.md` 按国内相关法规整理了选题禁区、措辞红线与处理留痕要求。
- **信源健康度监控**：每天记账各信源的有效条数，源静默失效时自动标红。

## 仓库结构

```
.claude-plugin/          插件与插件市场清单
skills/
├── caijing-kuaibao/         主流程：抓取→选题→核查→脚本→切片→门禁→PNG
│   ├── SKILL.md
│   ├── prompts/rules/       合规规则、文案风格圣经
│   ├── prompts/steps/       核查/旁白/切片三步的完整规则
│   ├── src/                 渲染、校验门禁、历史去重、信源健康度脚本
│   ├── config/templates.json  五套卡片配色模板
│   └── examples/show.config.example.json
├── caijing-broll-compose/   PNG卡片 + B-roll → 12秒成片，素材库打标入库，可扩展的素材补货框架
└── caijing-distribute/      YouTube / 视频号 / 今日头条分发 + 小红书手动包
requirements.txt
```

## 安装

需要先装好 [Claude Code](https://claude.com/claude-code)。

**方式一：插件市场（推荐，方便更新）**

在 Claude Code 里依次输入：
```
/plugin marketplace add jian25746-prog/caijing-kuaibao-skill
/plugin install caijing-kuaibao@caijing-kuaibao-skills
```

**方式二：直接复制**

```bash
git clone https://github.com/jian25746-prog/caijing-kuaibao-skill.git
cp -R caijing-kuaibao-skill/skills/* ~/.claude/skills/
```

**依赖**

| 用途 | 依赖 |
|---|---|
| 主流程抓取新闻 | Claude in Chrome 浏览器扩展（抓取与原文核查都通过它读取真实页面） |
| 渲染卡片 | Python 3.10+、`pip install Pillow` |
| 合成成片 | `ffmpeg` / `ffprobe`（macOS：`brew install ffmpeg`） |
| 多平台分发 | `pip install -r requirements.txt`，再 `playwright install chromium` |

卡片默认使用 macOS 自带中文字体（冬青黑体 / 华文黑体），Linux 下会回退到 Droid Sans Fallback。

## 兼容性

**目前只支持 Claude Code**（在 macOS 上实际运行；Linux 下卡片渲染有字体回退，但未实测；Windows 未测试）。

三个 skill 的情况不一样：

| Skill | 能否在其他 AI 工具里用 | 原因 |
|---|---|---|
| `caijing-kuaibao`（主流程） | **不能** | 依赖两项 Claude Code 专属能力：① **Claude in Chrome** 浏览器扩展，用于抓取新闻页面、回原文核实事实；② **派发 Sonnet / Opus 子代理**，分别执行事实核查和写稿。另外手动运行时的选题确认用到 `AskUserQuestion` 选择弹窗 |
| `caijing-broll-compose`（成片合成） | 理论上可以，未测试 | 流程只是读说明、运行 Python 脚本，不依赖 Claude 专属工具 |
| `caijing-distribute`（多平台分发） | 理论上可以，未测试 | 同上 |

- `SKILL.md` 本身是通用格式，Codex、Cursor 等支持 SKILL.md 的工具也许能加载，但主流程在那里跑不起来，**请不要把本仓库当作"跨平台 skill"**。
- 所有 Python 脚本（渲染、校验门禁、成片合成、各平台上传）都可以脱离任何 AI 工具直接在命令行运行，用法写在每个脚本开头的注释里。
- 欢迎为其他 AI 工具做适配：主流程需要替换的，正是上面列出的网页读取和子代理两项能力。

## 快速开始

1. **建一个节目工作区**，每天的产出和运行数据都放这里：
   ```bash
   mkdir -p ~/my-finance-show && cd ~/my-finance-show
   cp <skill目录>/caijing-kuaibao/examples/show.config.example.json show.config.json
   ```
   编辑 `show.config.json`，改成你自己的节目名、品牌、定位和风格。

2. **在工作区里启动 Claude Code**，确认 Chrome 已打开且 Claude 扩展已连接，然后说：
   > 跑今天的财经快报

   产出在 `~/my-finance-show/<M月D日>/`：`PNG素材/`、`今日播报内容.txt`、`首条评论.txt`、`工作区/制作日志.txt`。

3. **（可选）合成成片**：先准备你自己的素材库（见下文），然后：
   ```bash
   export BROLL_LIBRARY=~/my-broll-library
   ```
   > 合成今天的成片

4. **（可选）分发**：按 `skills/caijing-distribute/SKILL.md` 的【一次性准备】配置账号登录后：
   > 分发 7点财经快报 9月25日

   第一次务必先用 `--no-publish` 演练。

**环境变量一览**

| 变量 | 用途 | 默认值 |
|---|---|---|
| `CAIJING_WORKSPACE` | 节目工作区 | 当前目录 |
| `BROLL_LIBRARY` | B-roll 素材库根目录 | 必须设置 |
| `BROLL_ARCHIVE` | （可选）源视频归档目录，供重审工具找回原片 | 空 |
| `RESTOCK_SOURCES` | （可选）素材补货默认使用的下载渠道，逗号分隔 | 全部可用渠道 |
| `CAIJING_UPLOAD_HOME` | 分发用的账号登录态、密钥、发布物料目录 | `~/caijing-distribute` |
| `CAIJING_SHOW` | 播放数据回流对应的节目名 | `7点财经播报` |
| `CAIJING_WATERMARK` | 卡片底部水印文字（主流程会按配置的品牌自动传入） | `凡人投资 \| 仅供参考，不构成投资建议` |

## 换成你自己的节目

- **节目名 / 品牌 / 定位 / 风格 / 横幅文字**：改工作区里的 `show.config.json` 即可。
- **文案风格**：`prompts/rules/style_dna.md` 是作者节目的风格圣经，原样保留作为范例，建议按你的调性重写。
- **卡片配色与模板**：`config/templates.json`。
- **仍沿用作者品牌的深层标签**：研判卡里的「凡人研判强度」、模板名「T4_凡人速报」、合规规则里的"凡人视角"示例。它们散落在 `prompts/steps/step4_rules.md`、`src/validate_slices.py`、`config/templates.json` 中，需要时全文搜索"凡人"替换，注意三处保持一致（校验器会检查模板名）。

## B-roll 素材库

成片合成需要你自己的素材库。仓库**不附带任何素材**，只提供索引格式示例 `skills/caijing-broll-compose/examples/library.example.json`。用 `prep_broll.py scan` 扫描你的视频生成联系表，打标后 `prep_broll.py build` 入库，完整流程见该 skill 的 SKILL.md。

**自动补素材**：`tools/restock.py` 能按缺口关键词从你接入的渠道搜索、竖屏优先下载，并自动生成联系表、记录每条素材的出处和授权。它**不内置任何下载渠道**，从哪里下载由你决定：复制 `tools/sources/_template.py`、改名、实现一个搜索函数即可接入一个新渠道，放进目录就自动生效，主程序不用改。详见 [`tools/sources/README.md`](skills/caijing-broll-compose/tools/sources/README.md)。

## 合规与免责

- **不构成投资建议**：本工具生成的所有研判内容仅为个人观点，发布时请保留免责声明。
- **新闻来源**：选题阶段只读公开页面的标题；核查阶段只为最终入选的条目读取 RSS 摘要或正文首段（最多 500 字、用完即弃，落盘保留不超过 140 字）用于核实；写稿时具名引用来源媒体。请遵守各信源网站的使用条款。
- **资质**：在中国大陆以"报道"形式发布时政类新闻需要互联网新闻信息服务许可，`compliance.md` 已按个人自媒体无该资质的前提设置了选题禁区，请根据你自己的情况核对。
- **AI 生成内容标识**：按《人工智能生成合成内容标识办法》，含 AI 生成画面、配音或文字的内容需要标识。本工具**目前不会**自动添加 AI 标识角标，也不会自动勾选各平台的 AI 内容声明，请在发布时自行处理。
- **素材版权**：素材库和背景音乐只能使用你有权使用的内容。
- **平台规则**：各平台对自动化发布的限制不同且会变化。本工具对小红书只生成手动发布包，不做自动发布；使用其他平台的自动上传前请阅读并遵守平台条款，由此产生的账号风险由使用者自行承担。

## 已知限制

- 抓取依赖各新闻网站当前的页面结构，网站改版后对应的抓取脚本需要更新（`source_health.py` 会让这类失效尽快暴露）。
- 核查与写稿步骤会派发 Sonnet / Opus 子代理，消耗与模型选择由 Claude Code 决定。
- 分发脚本依赖各平台创作者后台的页面结构，后台改版时可能需要调整。

## 许可证

[MIT](LICENSE)
