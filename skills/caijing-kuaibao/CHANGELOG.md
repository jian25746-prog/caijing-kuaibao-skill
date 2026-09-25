# SKILL.md 变更史与实测记录归档

> 本文件**不参与任何运行时读取**，纯归档。SKILL.md 里只保留操作规则，"为什么/何时改的"叙述性记录移到这里，给未来排查回溯用。

---

## 2026-07-07 瘦身手术（备份：`_backup/*.20260707_072107.bak`）

- **删除三段死代码**（约 223 行 / 16KB）：STEP2「核查规则正文」、STEP3「脚本规则正文」、STEP4「切片规则正文」——subagent 实际只读 `prompts/steps/stepN_rules.md`，这三段无人读却每轮计费。删除前独有内容已迁移（见下）。
- **修复 list_items 断链**：「第六步：列表型标题内容填充」此前只存在于 SKILL.md 死段，STEP2 subagent 真读的 step2_rules.md 里没有 → `list_items` 从未被产出，STEP3/4 一直走摘要兜底。已迁入 step2_rules.md（所有条目执行、快速通道不豁免）+ STEP2 prompt JSON 增加该字段。
- **「具名实体强制落地」「列表型标题强制展开」迁入 step3/4_rules.md**：此前只写在 dispatch prompt 里、规则文件反而没有。
- **三个 dispatch prompt 模板去重瘦身**：与 stepN_rules.md 逐字重复的三阶段流程/自检清单/JSON格式等全部删除，模板只留：角色 + 读哪些文件 + 输出路径 + 硬约束（token红线/防注入/禁循环）+ summary 指针。STEP4 模板原引用"SKILL.md 切片规则正文/hook强制自检节"已改指 step4_rules.md（原引用有诱导 subagent 读全量 SKILL.md 的风险）。
- **制作日志落盘**：STEP4.5 完整日志改写入 `工作区/制作日志.txt`，对话框只出 5 行摘要。
- **删 STEP1 第七阶段重复 mkdir**（STEP 0D 已建同样目录），②③④重排为②③。
- **字数口径统一**：死段的"2500-3000字"废弃，以 step3_rules.md 的 2200-2800 为准。
- **模型标注去版本号**："Sonnet 4.6"/"Opus 4.7" → "Sonnet"/"Opus"（Agent 工具的 model 参数自动解析到最新版）。
- 效果：SKILL.md 81.3KB/1105行 → 52.0KB/743行（−36%）。

## 从 SKILL.md 移出的历史实测记录（原文归档）

### MarketWatch 移除（2026-06-14）
硬财经 feed（marketpulse/realtimeheadlines）停更近一年，唯一在更新的 topstories 全是个人理财软文，留之徒增筛水分负担。

### 界面新闻移除（2026-06-21）
URL 404 死链。step2_rules.md A级快速通道名单同步清理（2026-07-07 补做）。

### CNBC Economy 移除（2026-06-16）
id20910258 实测为死源：最新 06-12、4 天没更新、8h 窗口内 0 条。

### 国际源 JS 硬筛实测（2026-06-16）
CNBC 30 条按窗口过滤正确、time 转北京无误；华尔街见闻 100 条→窗口内按时段过滤、time 转北京无误。旧 get_page_text 读整页 RSS 约 20.5K token，JS 硬筛后 CNBC TopNews / SCMP 各约 0.5K + 华尔街见闻约 2.5K ≈ 3.5K，降约 17K。

### 国内五源 DOM 硬筛实测（2026-06-21 / 2026-06-23）
2026-06-21 财联社/财新/第一财经/21世纪/证券时报五源 JS 均 Chrome 实测通过；2026-06-23 补回 `link` 字段并实测：财联社/财新/第一财经/证券时报 100% 有具体文章链接；21世纪是快讯流无独立链接、`link` 固定为首页（正文已在列表、不影响选题，STEP2 按"快讯源·首页URL"处理、不判 URL_MISSING 瑕疵）。

### 更早关键事件
- 2026-06-11 PNG 路径漂移事故（"PNG素材"后缀粘连"目录"二字）→ STEP7 逐字铁律
- 2026-06-12 SCMP feed 频道纠错（rss/2 香港社会新闻 → rss/92 Business）
- 2026-06-13 VOO 旧闻事故 → STEP2 事件时效核查（发布时间≠事件首报时间）
- 2026-06-16 时区乌龙 → 抓取层 JS 硬筛根治（GMT/unix 秒统一转北京时间）
