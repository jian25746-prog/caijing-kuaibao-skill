#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sph_fetch_stats.py — 抓视频号已发视频播放数据 → 回流成受众偏好画像（选题闭环）。

原理：Playwright 复用 sph_profile 登录态打开视频号助手「内容管理」，
网络拦截 post_list 接口 JSON（比 DOM 选择器稳），翻页收集近 N 天视频的
播放/点赞/评论/转发；用「描述/短标题」匹配回 发布物料.json → clip →
成片文件名 slug（成片_01_石油.mp4 → 题材"石油"，确定性、零模糊）；
累积台账 + 按题材聚合（21天滚动·中位数·时间衰减）→ 观众偏好.json 供 4am STEP1 读。

用法:
  python3 sph_fetch_stats.py --days 7              # 抓近7天数据并入台账
  python3 sph_fetch_stats.py --days 7 --aggregate  # 抓完顺带重算观众偏好.json
  python3 sph_fetch_stats.py --aggregate-only      # 不抓取，只用现有台账重算
  python3 sph_fetch_stats.py --days 3 --debug      # 落盘原始接口JSON(_raw_post_list.json)供字段校准

铁律:
- 只读 物料/ 与节目目录产物；台账与偏好写到 7点财经播报/观众数据/（4am 沙盒 Read 需在节目目录内）。
- 任何失败只影响数据更新，绝不 raise 到调用方（distribute_all 挂尾用）。
"""
import os, sys, json, re, time, statistics
from datetime import datetime, timedelta

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
SHOW_ROOT = os.environ.get("CAIJING_WORKSPACE") or os.getcwd()  # 节目工作区（4am skill 读观众偏好的位置）
PROFILE = os.path.join(UPLOAD_ROOT, "config", "sph_profile")
MATERIAL = os.path.join(UPLOAD_ROOT, "物料", os.environ.get("CAIJING_SHOW", "7点财经播报"))
DATA_DIR = os.path.join(SHOW_ROOT, "观众数据")
LEDGER = os.path.join(DATA_DIR, "sph_stats.json")
PREF = os.path.join(DATA_DIR, "观众偏好.json")        # 只放爆款信号，供 4am 读
TAGSTAT = os.path.join(DATA_DIR, "题材统计.json")      # 题材统计，仅供人工复盘，不喂选题
LIST_URL = "https://channels.weixin.qq.com/platform/post/list"

HOT = 100_000         # 🔴 爆款线：达此播放量才触发次日跟进
VIRAL_WINDOW = 3      # 爆款检测窗口（天）：近N天发布且已破线（爆款需数日累积，故不止看昨天）
WINDOW_DAYS = 21      # 题材统计滚动窗口（仅复盘用）
HALF_LIFE = 7.0       # 时间衰减半衰期（天，仅复盘用）
MIN_N = 5             # 样本不足此数的题材不计权（仅复盘用）
COLD = 500            # 冷门线（仅复盘用）


def log(*a):
    print("[sph_stats]", *a, flush=True)


def norm(s):
    return re.sub(r"[\s，。,.！!？?#·、：:\-—…​]+", "", s or "")


# ---------- 发布物料索引：描述/短标题 → (date_dir, 播报序号, slug, 真实内容) ----------

def load_material_index(days_back=30):
    idx = []
    if not os.path.isdir(MATERIAL):
        return idx
    for d in sorted(os.listdir(MATERIAL)):
        p = os.path.join(MATERIAL, d, "发布物料.json")
        if not os.path.isfile(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for c in data.get("clips", []):
            video = c.get("video", "")
            m = re.search(r"成片_(\d+)_([^./]+)\.mp4", os.path.basename(video))
            slug = m.group(2) if m else None
            sph = c.get("shipinhao", {}) or {}
            idx.append({
                "date_dir": d,
                "clip_num": c.get("clip_num"),
                "播报序号": c.get("播报序号"),
                "真实内容": c.get("真实内容", ""),
                "slug": slug,
                "短标题": norm(sph.get("短标题", "")),
                "描述头": norm((sph.get("描述", "") or "")[:40]),
            })
    return idx


def match_material(idx, desc):
    """接口返回的 desc（发布时填的描述）匹配物料：描述头前缀 > 短标题包含"""
    nd = norm(desc)
    if not nd:
        return None
    for it in idx:
        if it["描述头"] and nd.startswith(it["描述头"][:24]):
            return it
    for it in idx:
        if it["短标题"] and it["短标题"] in nd:
            return it
    return None


# ---------- 抓取：网络拦截 post_list ----------

def fetch(days, debug=False):
    from playwright.sync_api import sync_playwright
    captured = []
    req_info = {}

    def on_response(resp):
        u = resp.url
        if "post_list" in u or "post/list" in u:
            try:
                j = resp.json()
                captured.append(j)
                if not req_info:
                    req_info.update({"url": u, "method": resp.request.method,
                                     "post_data": resp.request.post_data})
            except Exception:
                pass

    with sync_playwright() as p:
        kwargs = dict(user_data_dir=PROFILE, headless=False,
                      viewport={"width": 1440, "height": 900})
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kwargs)
        except Exception:
            ctx = p.chromium.launch_persistent_context(**kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", on_response)
        page.goto(LIST_URL, timeout=60_000)
        page.wait_for_timeout(6000)

        if "login" in page.url:
            log("⚠️ 登录态失效，需重新扫码（跑一次 shipinhao_upload.py 或 sph_open.py）")
            ctx.close()
            return None

        # 翻页：不点UI，直接同源重放页面自己的 post_list 请求、改 currentPage（比按钮选择器稳）
        cutoff = time.time() - days * 86400
        log(f"post_list请求: method={req_info.get('method')} url={str(req_info.get('url'))[:120]} "
            f"post_data={'有' if req_info.get('post_data') else '无'}")
        if req_info.get("post_data"):
            try:
                payload = json.loads(req_info["post_data"])
                page_key = next((k for k in ("currentPage", "pageNum", "page", "pageNo")
                                 if k in payload), None)
                log(f"payload键={list(payload.keys())} page_key={page_key}")
                # 先抓再判：每抓一页，用「该页自己的最老时间」决定是否继续
                # （第一页可能混入置顶旧视频，其时间不可作停止依据）
                for pg in range(2, 26):  # 上限25页=500条，防死循环
                    if not page_key:
                        break
                    payload[page_key] = pg
                    j = page.evaluate(
                        """async ([u, body]) => {
                            const r = await fetch(u, {method:'POST', credentials:'include',
                                headers:{'Content-Type':'application/json'},
                                body: JSON.stringify(body)});
                            return await r.json();
                        }""", [req_info["url"], payload])
                    n_page = sum(1 for _ in _iter_posts([j]))
                    if not n_page:
                        log(f"第{pg}页无数据，返回键={list(j.keys()) if isinstance(j,dict) else type(j)} "
                            f"errCode={j.get('errCode') if isinstance(j,dict) else '?'}")
                        break
                    captured.append(j)
                    oldest = _oldest_ts([j])
                    log(f"第{pg}页 {n_page}条，最老 {datetime.fromtimestamp(oldest).strftime('%m-%d') if oldest else '?'}")
                    if oldest and oldest < cutoff:
                        break
                    page.wait_for_timeout(800)
            except Exception as e:
                log(f"⚠️ 翻页失败（已抓 {len(captured)} 页照常处理）：{e}")
        ctx.close()

    if debug:
        os.makedirs(DATA_DIR, exist_ok=True)
        raw = os.path.join(DATA_DIR, "_raw_post_list.json")
        json.dump(captured, open(raw, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log(f"debug: 原始接口JSON已落盘 {raw}（{len(captured)} 次响应）")
    return captured


def _iter_posts(captured):
    for j in captured or []:
        data = j.get("data") if isinstance(j, dict) else None
        lst = None
        if isinstance(data, dict):
            for k in ("list", "postList", "objectList", "posts"):
                if isinstance(data.get(k), list):
                    lst = data[k]
                    break
        if lst is None and isinstance(j, dict) and isinstance(j.get("list"), list):
            lst = j["list"]
        for it in lst or []:
            if isinstance(it, dict):
                yield it


def _oldest_ts(captured):
    ts = [(_get_ts(p) or 0) for p in _iter_posts(captured)]
    ts = [t for t in ts if t]
    return min(ts) if ts else None


def _get_ts(post):
    for k in ("createTime", "create_time", "publishTime", "pubTime"):
        v = post.get(k)
        if isinstance(v, (int, float)) and v > 1e9:
            return int(v)
    return None


def _get_desc(post):
    d = post.get("desc")
    if isinstance(d, dict):
        d = d.get("description") or d.get("desc") or ""
    if not isinstance(d, str):
        d = post.get("description") or post.get("title") or ""
    return d


def _num(post, *keys):
    """在 post 及其一层嵌套 dict 里找第一个命中的计数字段"""
    pools = [post] + [v for v in post.values() if isinstance(v, dict)]
    for pool in pools:
        for k in keys:
            v = pool.get(k)
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str) and v.isdigit():
                return int(v)
    return None


def parse_posts(captured, days):
    cutoff = time.time() - days * 86400
    out, seen = [], set()
    for post in _iter_posts(captured):
        ts = _get_ts(post)
        if not ts or ts < cutoff:
            continue
        oid = str(post.get("objectId") or post.get("exportId") or post.get("id") or ts)
        if oid in seen:
            continue
        seen.add(oid)
        out.append({
            "id": oid,
            "publish_ts": ts,
            "publish_date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
            "desc": _get_desc(post)[:60],
            "views": _num(post, "readCount", "readNum", "playCount", "playNum", "browseCount"),
            "likes": _num(post, "likeCount", "likeNum", "praiseCount"),
            "comments": _num(post, "commentCount", "commentNum"),
            "forwards": _num(post, "forwardCount", "forwardNum", "shareCount"),
            # 🔴 算法核心指标（2026-08-01 发现接口本就返回，无需翻后台）
            "full_play_rate": post.get("fullPlayRate"),          # 完播率——视频号推荐的第一权重
            "avg_play_sec": post.get("avgPlayTimeSec"),          # 平均播放时长
            "is_declared": (post.get("originalInfo") or {}).get("isDeclared"),   # 原创声明 1=已声明
            "audit_flag": (post.get("originalInfo") or {}).get("auditOriginalFlag"),
            "is_disabled": (post.get("disableInfo") or {}).get("isDisabled"),    # 是否被封禁/限制
            "status": post.get("status"),
        })
    return out


# ---------- 台账 ----------

def update_ledger(posts, idx):
    os.makedirs(DATA_DIR, exist_ok=True)
    ledger = {}
    if os.path.isfile(LEDGER):
        try:
            ledger = json.load(open(LEDGER, encoding="utf-8"))
        except Exception:
            log("⚠️ 台账损坏，重建")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    matched = 0
    for p in posts:
        rec = ledger.get(p["id"], {})
        if "slug" not in rec:
            m = match_material(idx, p["desc"])
            if m:
                rec.update({"date_dir": m["date_dir"], "播报序号": m["播报序号"],
                            "真实内容": m["真实内容"], "slug": m["slug"]})
        if rec.get("slug"):
            matched += 1
        rec.update({"publish_ts": p["publish_ts"], "publish_date": p["publish_date"],
                    "desc": p["desc"], "views": p["views"], "likes": p["likes"],
                    "comments": p["comments"], "forwards": p["forwards"],
                    "full_play_rate": p["full_play_rate"], "avg_play_sec": p["avg_play_sec"],
                    "is_declared": p["is_declared"], "audit_flag": p["audit_flag"],
                    "is_disabled": p["is_disabled"], "status": p["status"],
                    "last_fetch": now})
        ledger[p["id"]] = rec
    tmp = LEDGER + ".tmp"
    json.dump(ledger, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, LEDGER)
    log(f"台账更新：本次 {len(posts)} 条（含题材匹配 {matched}），累计 {len(ledger)} 条 → {LEDGER}")
    return ledger


# ---------- 爆款检测 → 观众偏好.json（4am 唯一消费的文件） ----------

def detect_viral(ledger):
    """只输出「近 VIRAL_WINDOW 天内发布且播放 ≥ HOT」的爆款。
    没有爆款 → 有效=false，4am 完全不参考受众数据、回落热点词库。
    设计依据（2026-08-01 实测 258 样本）：题材中位数是噪音（同题材内 23～105万），
    只有 10万+ 才是真信号；10万+ 占比 0.4%，故本文件绝大多数日子是「无爆款」。"""
    now = time.time()
    cutoff = now - VIRAL_WINDOW * 86400
    virals = []
    for rec in ledger.values():
        if (rec.get("views") or 0) < HOT:
            continue
        if (rec.get("publish_ts") or 0) < cutoff:
            continue
        virals.append({
            "标题": rec.get("真实内容") or rec.get("desc", "")[:40],
            "题材": rec.get("slug"),
            "播放量": rec["views"],
            "发布日": rec.get("publish_date", "")[:10],
            "来源日期目录": rec.get("date_dir"),
            "播报序号": rec.get("播报序号"),
        })
    virals.sort(key=lambda x: -x["播放量"])

    pref = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "有效": bool(virals),
        "爆款门槛": HOT,
        "检测窗口天数": VIRAL_WINDOW,
        "爆款": virals,
        "usage": (
            "【有效=false】→ 本日无爆款：**完全不参考受众数据**，选题按原有热点词库热度（weighted）执行，不做任何受众加权。\n"
            "【有效=true】→ 近日出现≥10万播放爆款：STEP1 第六阶段**优先跟进**——① 该爆款事件的后续进展 "
            "② 同题材（见'题材'字段）的相关新闻；在10条内留 1-2 条给跟进条目。\n"
            "铁律：① 候选池里确实没有相关新闻就**不硬凑**（宁缺毋滥，禁为跟进而编）；"
            "② 跟进受 STEP 0B 48小时排重约束，不得重播同一条；③ 爆款只提高相关题材优先级，不禁其他题材。"
        ),
    }
    tmp = PREF + ".tmp"
    json.dump(pref, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, PREF)
    if virals:
        log(f"🔥 检测到 {len(virals)} 条爆款（≥{HOT:,}）→ 明日跟进：")
        for v in virals:
            log(f"   {v['播放量']:,} | {v['题材']} | {v['发布日']} | {v['标题'][:30]}")
    else:
        log(f"无爆款（近{VIRAL_WINDOW}天无≥{HOT:,}）→ 明日按原有热点词库选题，不参考受众数据")
    return pref


# ---------- 题材统计（仅人工复盘，不喂选题） ----------

def aggregate(ledger):
    now = time.time()
    cutoff = now - WINDOW_DAYS * 86400
    by_slug = {}
    total = 0
    for rec in ledger.values():
        if not rec.get("slug") or rec.get("views") is None:
            continue
        if (rec.get("publish_ts") or 0) < cutoff:
            continue
        age_d = (now - rec["publish_ts"]) / 86400
        w = 0.5 ** (age_d / HALF_LIFE)
        by_slug.setdefault(rec["slug"], []).append((rec["views"], w))
        total += 1

    tags = {}
    for slug, arr in by_slug.items():
        views = [v for v, _ in arr]
        wsum = sum(w for _, w in arr)
        wmean = sum(v * w for v, w in arr) / wsum if wsum else 0
        tags[slug] = {
            "n": len(views),
            "median": int(statistics.median(views)),
            "wmean": int(wmean),
            "hot_rate": round(sum(1 for v in views if v >= HOT) / len(views), 2),
            "cold_rate": round(sum(1 for v in views if v < COLD) / len(views), 2),
        }

    ranked = [s for s in sorted(tags, key=lambda s: -tags[s]["median"]) if tags[s]["n"] >= MIN_N]
    k = max(1, len(ranked) // 3) if ranked else 0
    top, cold = ranked[:k], ranked[-k:] if k else []
    for s in tags:
        tags[s]["tier"] = "top" if s in top else ("cold" if s in cold else
                          ("low_n" if tags[s]["n"] < MIN_N else "mid"))

    stat = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "⚠️用途": "仅供人工复盘题材表现，**不参与选题**（2026-08-01 改：题材中位数被实测证明是噪音，"
                  "同题材内播放量跨度 23～105万，故选题只认爆款信号、见 观众偏好.json）",
        "window_days": WINDOW_DAYS, "half_life_days": HALF_LIFE,
        "sample_total": total, "min_n": MIN_N,
        "tags": tags, "top_tags": top, "cold_tags": cold,
    }
    tmp = TAGSTAT + ".tmp"
    json.dump(stat, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, TAGSTAT)
    log(f"题材统计（仅复盘）：{len(tags)} 题材 / {total} 样本 → {TAGSTAT}")
    return stat


def main():
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7
    debug = "--debug" in sys.argv
    agg_only = "--aggregate-only" in sys.argv
    do_agg = "--aggregate" in sys.argv or agg_only

    try:
        if agg_only:
            ledger = json.load(open(LEDGER, encoding="utf-8")) if os.path.isfile(LEDGER) else {}
        else:
            captured = fetch(days, debug=debug)
            if captured is None:
                sys.exit(0)  # 登录失效已提示，不算崩
            posts = parse_posts(captured, days)
            if not posts:
                log("⚠️ 未解析到任何视频数据（接口字段可能变更，用 --debug 落盘原始JSON核查）")
                sys.exit(0)
            idx = load_material_index()
            ledger = update_ledger(posts, idx)
        if do_agg and ledger:
            detect_viral(ledger)   # → 观众偏好.json（4am 消费：只认爆款信号）
            aggregate(ledger)      # → 题材统计.json（仅人工复盘）
    except SystemExit:
        raise
    except Exception as e:
        log(f"⚠️ 失败（不影响发布流程）：{type(e).__name__}: {e}")
        sys.exit(0)


if __name__ == "__main__":
    main()
