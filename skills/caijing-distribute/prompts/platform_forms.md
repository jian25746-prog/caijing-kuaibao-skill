# 三平台表单规范 + 发布物料映射

> ⚠️ **零幻觉提醒**：下面标 ⚠️ 的字段名 / 字数上限来自通用认知，**必须在阶段0 用 Chrome `javascript_tool` 进真实后台逐字核实后改成 ✅**。标 ✅ 的是已核实的。YouTube 走 API、字段稳定，已 ✅。

每条 clip 的 `发布物料.json` 结构：`{clip_num, 真实内容, 播报序号, video, cover, pinned_comment, shipinhao{}, xiaohongshu{}, youtube{}}`。

---

## 1) 微信视频号　后台 `channels.weixin.qq.com/platform/post/create`
| 表单字段 | 来源 | 规则 |
|---|---|---|
| 视频文件 | `clip.video` | 上传 第X条.mp4 |
| 描述 ⚠️ | `shipinhao.描述` | 完整公式标题作首句 + 1–2 句事实 + `#话题`；#话题写在描述里 |
| 短标题 ⚠️(约6–16字) | `shipinhao.短标题` | 压缩版标题 |
| 封面 ⚠️ | `cover` | 上传 封面_第X条.png |
| 合集 ⚠️ | `shows.json.collection`「7点财经快报」 | 固定 |
| 声明原创 ⚠️ | — | 勾选 |
| 位置 | — | 留空 |
| 定时发布 ⚠️ | `shows.json.schedule_time` 07:00 | 当天 07:00 |
| 置顶首评 | `pinned_comment` | **发布后**在评论区发该条并置顶 |

## 2) 小红书　后台 `creator.xiaohongshu.com/publish/publish`
| 表单字段 | 来源 | 规则 |
|---|---|---|
| 视频 | `clip.video` | 上传 |
| 标题 🔴**硬性≤20字** | `xiaohongshu.标题` | 公式标题直接用；**写入JSON前必须 len()自检**，超字数会让发布键失效、需人工介入（2026-07-01 实锤：34字标题卡住，用户手动干预才发出） |
| 正文 ⚠️(≤1000字) | `xiaohongshu.正文` | 分段 + emoji + 结尾"评论区聊聊"引导；`#话题#` 放正文末 |
| 话题(#) ⚠️ | `xiaohongshu.话题` | 映射自 today_entries 的 topic_tags + 固定 #财经 #财经早知道 |
| 封面 ⚠️ | `cover` | 上传 封面_第X条.png（或平台内选帧后定位到同帧） |
| 权限 / 定时 ⚠️ | 公开 / 07:00 | 固定 |
| 评论 | `pinned_comment` | 发布后评论区发 |

## 3) YouTube　Data API v3（`src/youtube_upload.py`）
| API 字段 | 来源 | 规则 |
|---|---|---|
| snippet.title ✅(≤100) | `youtube.title` | 公式标题 + `｜7点财经快报 #Shorts` |
| snippet.description ✅ | `youtube.description` | 简述 + 关注引导 + #标签 |
| snippet.tags ✅ | `youtube.tags` | topic_tags 映射 |
| snippet.categoryId ✅ | 固定 `25` | News & Politics |
| status.selfDeclaredMadeForKids ✅ | 固定 `false` | 受众：非儿童（法务必填） |
| status.privacyStatus + publishAt ✅ | private + 当天07:00(RFC3339) | 定时发布 |
| thumbnails.set ✅ | `cover` | 封面_第X条.png |
| playlistItems ✅ | 「7点财经快报」 | 加入播放列表（无则建） |
| 置顶评论 ✅发/⚠️置顶 | `pinned_comment` | API 可发评论；置顶 API 受限、可能需 Studio 手点 |

---

## 字段填写顺序铁律（视频号 / 小红书 走 Chrome）
1. 用 `javascript_tool` 读当前表单的真实字段（label/placeholder/maxlength），**禁 get_page_text 全页**。
2. 逐字段填入 → 上传封面 → 设定时 07:00。
3. **填满后停在「发布/定时」键**，把该条的标题/封面/首评回报给用户，等"确认"再点。
4. 发布成功后，去评论区发 `pinned_comment` 并置顶。
5. 下一条 clip 重复。
