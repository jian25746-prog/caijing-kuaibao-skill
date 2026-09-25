# 如何添加素材下载渠道

`restock.py`（素材补货）本身**不内置任何下载渠道**。你想从哪里找素材——免版权素材网站、自己的网盘或 NAS、公司的素材库，或者任何其他来源——都可以写成一个"下载源"接进来。一个渠道一个文件，放进本目录就自动生效，`restock.py` 一行都不用改。

## 三步接入

**1. 复制模板**

```bash
cd tools/sources
cp _template.py mysite.py        # 文件名随意，但不能以下划线开头
```

**2. 填写 `mysite.py`**

- 把 `name` 改成一个小写英文名（如 `"mysite"`），这是命令行里用的名字。**`name` 留空时不会被加载。**
- 实现 `search(query, n, portrait_only)`：按关键词搜索，返回 `Candidate` 列表（字段说明见 `base.py`）。
- 如果直接访问 `download_url` 就能拿到 mp4，不用写 `download()`；需要签名链接、登录或调用命令行工具时，再覆盖 `download(cand, dest_path)`。
- 需要 API 密钥时，把环境变量名写进 `env_key`（如 `"MYSITE_API_KEY"`），用 `self.api_key()` 读取；**不要把密钥写进代码**。

**3. 自检**

```bash
python3 restock.py sources                          # 新渠道应出现并显示"✅ 可用"
python3 restock.py search "city skyline" --source mysite
```

## 接口一览（`base.py`）

| 成员 | 说明 |
|---|---|
| `name` / `display_name` / `homepage` | 渠道标识、展示名、网站地址 |
| `env_key` / `signup_url` | 所需密钥的环境变量名、申请地址（没配时会提示用户） |
| `license` / `license_url` / `notes` | 授权名称、授权页面、使用限制摘要，`restock.py sources` 会展示 |
| `search(query, n, portrait_only)` | **必须实现**。返回 `[Candidate]`；网站不支持按方向筛选时，在本地用 `c.vertical` 过滤 |
| `download(cand, dest_path)` | 可选。默认直接 HTTP 下载 `cand.download_url`；函数结束时 `dest_path` 必须是完整的 mp4，失败就抛异常 |
| `self.get_json(url, params, headers)` | 工具方法：请求 JSON 接口，自带 24 小时缓存，同样的搜索不会重复打接口 |
| `available()` | 可选覆盖。默认只检查 `env_key` 对应的密钥是否已设置 |

`Candidate` 必填字段：`source`（写 `self.name`）、`id`（该渠道内的唯一 ID）、`title`、`page_url`、`download_url`、`width`、`height`、`duration`；`author` / `author_url` / `license` / `license_url` 选填，但**强烈建议填写**，下载时会自动记进出处台账。

## 主程序替你做了什么

- **汇总多个渠道**：默认搜索所有可用渠道，也可以用 `--source a,b` 或环境变量 `RESTOCK_SOURCES=a,b` 指定
- **竖屏优先**：结果按"竖屏在前、时长短的在前"排序，超过 10 分钟的自动跳过
- **跨次去重**：`{素材库}/_incoming/_downloaded.json` 以 `渠道名:ID` 为键，下载过的不会再下
- **出处台账**：每条下载都会在上面的台账里记录页面地址、作者、授权，方便日后核对来源、署名
- **接上审片流程**：下载后自动调用 `prep_broll.py scan` 生成联系表，你审片后再入库

## 使用责任

你接入的每一个渠道，由你自己确认：

- **素材授权**：你是否有权把这些画面用在自己的视频里，尤其是可能变现的内容。
- **网站规则**：遵守该网站的服务条款和接口限制（请求频率、是否允许批量下载、是否需要注明出处等），把限制写进 `notes`，方便自己和他人查看。
- **人物与商标**：画面中可识别的人物、品牌商标，往往有额外的使用限制。

本项目只提供接入框架，不对任何具体渠道的内容授权负责。
