#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
restock.py — 素材库补货：按关键词从已接入的下载源搜索 → 竖屏优先下载 → 自动生成联系表待审片。

本工具不内置任何下载渠道。下载源放在 tools/sources/ 目录，一个渠道一个文件，
复制 sources/_template.py 改名填写即可接入，说明见 sources/README.md。

用法：
  python3 restock.py sources                                  # 列出已接入的下载源及是否可用
  python3 restock.py gaps                                     # 列出素材缺口清单里未解决的缺口
  python3 restock.py search "关键词" [--source a,b] [--n 12] [--landscape]
                                                              # 搜索，竖屏优先、时长升序
  python3 restock.py fetch <源:ID> --name 缺口名                # 下载上一次搜索结果里的某一条
  python3 restock.py auto "关键词" --name 缺口名 [--top 2] [--source a,b] [--landscape]
                                                              # 搜索 → 自动挑前N条竖屏下载 → 生成联系表

约定：
  • 素材库根目录 = 环境变量 BROLL_LIBRARY
  • 下载落地 {库}/_incoming/<日期>_<缺口名>/，联系表落地 {库}/_review/补货_<缺口名>/
  • {库}/_incoming/_downloaded.json 记录已下载条目（跨次去重）及每条的出处、作者、授权
  • 下载源选择：--source 指定；否则用环境变量 RESTOCK_SOURCES（逗号分隔）；都没有则用全部可用源
  • 可选：{库}/restock_keywords.json 维护「缺口名 → 搜索词列表」，gaps 命令会据此给出建议
  • 下载后仍需人工审片、写 decisions 后用 prep_broll.py build 入库，本工具不自动入库
"""
import datetime
import json
import os
import re
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
from sources import discover  # noqa: E402

LIBROOT = os.path.abspath(os.environ.get("BROLL_LIBRARY") or os.path.join(TOOLS, ".."))
INCOMING = os.path.join(LIBROOT, "_incoming")
GAPFILE = os.path.join(LIBROOT, "_review", "素材缺口清单.md")
DLLOG = os.path.join(INCOMING, "_downloaded.json")
LAST_SEARCH = os.path.join(INCOMING, "_last_search.json")
CACHE_DIR = os.path.join(INCOMING, "_search_cache")
KEYWORDS_FILE = os.path.join(LIBROOT, "restock_keywords.json")
MAX_DUR = 600  # 秒；超过10分钟的源太重，跳过


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _opt(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def _pick_sources(arg):
    found, errors = discover()
    for mod, err in errors.items():
        print(f"⚠️ 下载源 {mod}.py 加载失败，已跳过：{err}")
    wanted = arg or os.environ.get("RESTOCK_SOURCES", "")
    names = [x.strip() for x in wanted.split(",") if x.strip()] or list(found)
    picked = []
    for n in names:
        s = found.get(n)
        if not s:
            print(f"⚠️ 没有名为 {n} 的下载源（用 restock.py sources 查看已接入的源）")
        elif not s.available():
            print(f"⚠️ 下载源 {n} 不可用：需要设置环境变量 {s.env_key}" + (f"（申请：{s.signup_url}）" if s.signup_url else ""))
        else:
            s.cache_dir = CACHE_DIR
            picked.append(s)
    if not found:
        print("尚未接入任何下载源。复制 tools/sources/_template.py 改名填写即可，说明见 tools/sources/README.md")
    return picked


def cmd_sources():
    found, errors = discover()
    if not found and not errors:
        print("尚未接入任何下载源。复制 tools/sources/_template.py 改名填写即可，说明见 tools/sources/README.md")
        return
    for name, s in sorted(found.items()):
        state = "✅ 可用" if s.available() else f"⛔ 缺少环境变量 {s.env_key}"
        print(f"◆ {name}  {s.display_name or ''}  {state}")
        if s.homepage:
            print(f"    网站: {s.homepage}")
        if s.license:
            print(f"    授权: {s.license} {s.license_url}")
        if s.notes:
            print(f"    限制: {s.notes}")
        if not s.available() and s.signup_url:
            print(f"    申请密钥: {s.signup_url}")
    for mod, err in errors.items():
        print(f"⚠️ {mod}.py 加载失败：{err}")


def cmd_gaps():
    if not os.path.exists(GAPFILE):
        print(f"没有缺口清单：{GAPFILE}（成片合成遇到素材缺口时会自动登记到这里）")
        return []
    with open(GAPFILE, encoding="utf-8") as f:
        t = f.read()
    kwmap = _load_json(KEYWORDS_FILE, {})

    def suggest(name):
        for k, v in kwmap.items():
            if k.lower() in name.lower():
                return v
        return [name]

    open_gaps = []
    # 手工维护区：清单开头的 Markdown 表格（| 缺口 | 场景 | 状态 |），状态含 ✅ 或名称被 ~~划掉~~ 视为已解决
    head = t.split("## 二、")[0]
    for name, scene, status in re.findall(r"^\|\s*([^|~].*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$", head, re.M):
        name = name.replace("**", "").strip()
        if "✅" in status or name.startswith(("--", "实体/元素", "缺口")):
            continue
        open_gaps.append(name)
        print(f"  ◆ {name}\n     触发: {scene[:50]}\n     建议搜索: {suggest(name)}")
    # 自动登记区：compose_day.py 写入的 "## M月D日 (登记于YYYY-MM-DD)" 段落，只看近7天
    today = datetime.date.today()
    auto = set()
    for m in re.finditer(r"^## .*?\(登记于(\d{4}-\d{2}-\d{2})\)\n((?:- .*\n?)*)", t, re.M):
        try:
            if (today - datetime.date.fromisoformat(m.group(1))).days > 7:
                continue
        except ValueError:
            continue
        for ent in re.findall(r"点名\[([^\]]+)\]", m.group(2)):
            auto.update(e.strip() for e in ent.split("/"))
        auto.update(x.strip() for x in re.findall(r"缺口：(.+)$", m.group(2), re.M))
    for name in sorted(auto):
        if name and name not in open_gaps:
            open_gaps.append(name)
            print(f"  ◆ {name}（近7天自动登记）\n     建议搜索: {suggest(name)}")
    print(f"\n共 {len(open_gaps)} 个未解决缺口")
    return open_gaps


def _search(query, n, sources, landscape):
    cands = []
    for s in sources:
        try:
            got = s.search(query, n, portrait_only=not landscape)
        except Exception as e:
            print(f"  ⚠️ {s.name} 搜索失败：{type(e).__name__}: {e}")
            continue
        cands += [c for c in got if 0 < c.duration <= MAX_DUR]
    cands.sort(key=lambda c: (not c.vertical, c.duration))
    return cands


def cmd_search(query, n, source_arg, landscape):
    sources = _pick_sources(source_arg)
    if not sources:
        return []
    cands = _search(query, n, sources, landscape)
    dl = _load_json(DLLOG, {})
    print(f"=== 搜索: {query}  （{'含横屏' if landscape else '只看竖屏'}，≤{MAX_DUR}s）===")
    for s in sources:
        print(f"  来源: {s.display_name or s.name} {s.homepage}")
    for c in cands:
        mark = "📱9:16" if c.vertical else "🖥16:9"
        seen = " [已下过]" if c.key in dl else ""
        print(f"  {mark} {c.duration:5.0f}s {c.width}x{c.height}  {c.key}  {c.title[:60]}{seen}\n        {c.page_url}")
    if not cands:
        print("  (无符合条件的结果)")
    _save_json(LAST_SEARCH, {"query": query, "candidates": [c.to_dict() for c in cands]})
    return cands


def _fetch_one(cand, name, date, query=""):
    found, _ = discover()
    src = found.get(cand.source)
    if not src:
        print(f"  ❌ 找不到下载源 {cand.source}")
        return None
    dl = _load_json(DLLOG, {})
    if cand.key in dl:
        print(f"  ⏭ {cand.key} 已在 {dl[cand.key]['date']} 下载过（{dl[cand.key]['name']}），跳过")
        return None
    dest_dir = os.path.join(INCOMING, f"{date}_{name}")
    os.makedirs(dest_dir, exist_ok=True)
    safe_id = re.sub(r"[^\w.-]", "_", str(cand.id))[:60]
    path = os.path.join(dest_dir, f"{name}_{cand.source}_{safe_id}.mp4")
    try:
        src.download(cand, path)
    except Exception as e:
        print(f"  ❌ {cand.key} 下载失败：{type(e).__name__}: {e}")
        return None
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        print(f"  ❌ {cand.key} 下载后文件不存在或为空")
        return None
    dl[cand.key] = {"date": date, "name": name, "file": path, "query": query,
                    "page_url": cand.page_url, "author": cand.author, "author_url": cand.author_url,
                    "license": cand.license, "license_url": cand.license_url}
    _save_json(DLLOG, dl)
    credit = f"{cand.author} / {src.display_name or src.name}" if cand.author else (src.display_name or src.name)
    print(f"  ✅ {os.path.basename(path)}  ({os.path.getsize(path) // 1048576}MB)  出处: {credit} {cand.page_url}")
    return path


def _scan(name, paths):
    for p in paths:
        subprocess.run([sys.executable, os.path.join(TOOLS, "prep_broll.py"), "scan", p,
                        "--date", f"补货_{name}"], timeout=600)
    print(f"📋 联系表: {os.path.join(LIBROOT, '_review', '补货_' + name)}/  → 审片后写 decisions.json，用 prep_broll.py build 入库")


def cmd_fetch(key, name):
    from sources import Candidate
    last = _load_json(LAST_SEARCH, {})
    hit = next((c for c in last.get("candidates", []) if c.get("key") == key), None)
    if not hit:
        print(f"上一次搜索结果里没有 {key}，请先运行 restock.py search")
        return
    hit = {k: v for k, v in hit.items() if k != "key"}
    date = datetime.date.today().strftime("%Y%m%d")
    path = _fetch_one(Candidate(**hit), name, date, last.get("query", ""))
    if path:
        _scan(name, [path])


def cmd_auto(query, name, top, source_arg, landscape):
    date = datetime.date.today().strftime("%Y%m%d")
    cands = cmd_search(query, 12, source_arg, landscape)
    dl = _load_json(DLLOG, {})
    fresh = [c for c in cands if c.key not in dl]
    picks = [c for c in fresh if c.vertical][:top]
    if landscape and len(picks) < top:
        picks += [c for c in fresh if not c.vertical][:top - len(picks)]
    if not picks:
        if not cands:
            print("⚠️ 没有搜到结果，换个关键词试试")
        elif not fresh:
            print("⚠️ 搜到的结果都已下载过，换个关键词试试")
        else:
            print("⚠️ 没有未下载过的竖屏结果；加 --landscape 可放宽到横屏")
        return
    print(f"\n=== 自动下载 {len(picks)} 条 ===")
    got = [p for p in (_fetch_one(c, name, date, query) for c in picks) if p]
    if got:
        _scan(name, got)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    src_arg = _opt("--source")
    land = "--landscape" in sys.argv
    if cmd == "sources":
        cmd_sources()
    elif cmd == "gaps":
        cmd_gaps()
    elif cmd == "search" and len(sys.argv) > 2:
        cmd_search(sys.argv[2], int(_opt("--n", 12)), src_arg, land)
    elif cmd == "fetch" and len(sys.argv) > 2:
        cmd_fetch(sys.argv[2], _opt("--name", "未命名"))
    elif cmd == "auto" and len(sys.argv) > 2:
        cmd_auto(sys.argv[2], _opt("--name", "未命名"), int(_opt("--top", 2)), src_arg, land)
    else:
        print(__doc__)
