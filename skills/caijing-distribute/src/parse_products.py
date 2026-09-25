#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_products.py — 把某节目某天的 4am 产物解析成「逐条结构化」中间件 clips_raw.json，
供上传 skill 的模型步骤据此写三平台文案 + 按内容匹配首评/头条。

用法:  python3 parse_products.py <节目名> <日期目录>
例:    python3 parse_products.py 7点财经播报 6月13日

铁律:
- 只读节目目录，产物写到 节目上传/物料/<节目>/<日期>/clips_raw.json，绝不写节目目录。
- clip 文件名 第X条.mp4 的 X = 播报序号；今日播报内容.txt 是唯一可靠的「播报序号」基准。
- 首条评论/头条图文/today_entries 的顺序都 ≠ 播报序号 → 本脚本只把它们整池输出，
  由模型按「新闻内容」匹配到对应 clip.seq（不可按编号对齐，会张冠李戴）。
"""
import sys, os, re, json, glob

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
CONFIG = os.path.join(UPLOAD_ROOT, "config", "shows.json")

CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
          '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def cn_to_int(s):
    """中文数字 一..十(及 十几/几十 兜底) → int"""
    if s == '十':
        return 10
    if s.startswith('十'):
        return 10 + CN_NUM.get(s[1:], 0)
    if s.endswith('十'):
        return CN_NUM.get(s[:-1], 0) * 10
    if '十' in s:
        a, b = s.split('十')
        return CN_NUM.get(a, 0) * 10 + (CN_NUM.get(b, 0) if b else 0)
    return CN_NUM.get(s, 0)


def read_text(path):
    return open(path, encoding='utf-8').read() if os.path.exists(path) else ""


def parse_broadcast(path):
    """今日播报内容.txt → {seq: {broadcast_title, narration}}，按 第X条 切分。
    title = 该条第一句（到第一个。为止），narration = 从该条标题段到下一个「第X条」前的所有段落拼接。
    2026-09-12 起格式变化：「第X条」后分隔符从空格变逗号、且标题单独成段、正文另起多段
    （原假设"标题+正文挤在同一段"失效致 broadcast_items 全部落空）——现兼容两种格式：
    分隔符放宽为 逗号/顿号/空格 都认；不再要求标题和正文同段，改为跨段落累积到下一条标题前。"""
    items = {}
    text = read_text(path)
    if not text:
        return items
    pat = re.compile(r'^第([一二三四五六七八九十]+)条[，,、\s　]*', re.S)
    paras = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    cur_seq, cur_parts = None, []

    def flush():
        if cur_seq is not None and cur_parts:
            rest = '\n'.join(cur_parts).strip()
            title = re.split(r'[。\n]', rest, 1)[0].strip()
            items[cur_seq] = {"broadcast_title": title, "narration": rest}

    for para in paras:
        m = pat.match(para)
        if m:
            flush()
            cur_seq = cn_to_int(m.group(1))
            cur_parts = [para[m.end():].strip()]
        elif cur_seq is not None:
            cur_parts.append(para)
    flush()
    return items


def parse_first_comments(path):
    """首条评论.txt → [{idx, head, text}]，整池输出（顺序≠播报序号）。"""
    out = []
    text = read_text(path)
    if not text:
        return out
    pat = re.compile(r'^【第0?(\d+)条\s*(.*?)】\s*(.*)$', re.S)
    for block in re.split(r'\n\s*\n', text):
        block = block.strip()
        m = pat.match(block)
        if not m:
            continue
        out.append({"idx": int(m.group(1)),
                    "head": m.group(2).strip(),
                    "text": m.group(3).strip().replace('\n', ' ')})
    return out


def parse_toutiao(path):
    """衍生内容/头条图文.txt → [{title, body, img}]，民生向标题池（顺序≠播报序号）。"""
    out = []
    text = read_text(path)
    if not text:
        return out
    for block in re.split(r'■\s*头条\d+', text):
        block = block.strip()
        if not block:
            continue
        title = re.search(r'标题：(.*)', block)
        body = re.search(r'正文：(.*?)(?:\n配图：|\Z)', block, re.S)
        img = re.search(r'配图：(.*)', block)
        if title:
            out.append({"title": title.group(1).strip(),
                        "body": (body.group(1).strip() if body else ""),
                        "img": (img.group(1).strip() if img else "")})
    return out


def main():
    if len(sys.argv) < 3:
        print("用法: python3 parse_products.py <节目名> <日期目录>")
        sys.exit(1)
    show, date = sys.argv[1], sys.argv[2]
    _all = json.load(open(CONFIG, encoding='utf-8'))
    _shows = {k: v for k, v in _all.items() if not k.startswith('_')}
    cfg = _shows.get(show)
    if not cfg:  # 试别名（如 "7点财经快报" → 文件夹名 "7点财经播报"）
        for _k, _c in _shows.items():
            if show in _c.get('aliases', []):
                show, cfg = _k, _c
                break
    if not cfg:
        print(f"✗ shows.json 未配置节目: {sys.argv[1]}")
        sys.exit(1)
    ddir = os.path.join(cfg["base"], date)
    if not os.path.isdir(ddir):
        print(f"✗ 日期目录不存在: {ddir}")
        sys.exit(1)

    # 当天分条 clip：支持两种命名 ①第X条.mp4（中文数字，剪映手工版） ②成片_XX_主题.mp4（阿拉伯数字，B-roll流水线版，2026-07-03起）
    # 编号 X 两种命名下都 = 首条评论序号（≠播报序号，锚定规则不变），用户可只做子集、编号可非连续
    clip_pat_cn = re.compile(r'第([一二三四五六七八九十]+)条')
    clip_pat_num = re.compile(r'成片_0*(\d+)')
    globs = cfg["clip_glob"] if isinstance(cfg["clip_glob"], list) else [cfg["clip_glob"]]
    seen, clips = set(), []
    for pat in globs:
        for fp in glob.glob(os.path.join(ddir, pat)):
            if fp in seen:
                continue
            seen.add(fp)
            base = os.path.basename(fp)
            m = clip_pat_cn.search(base)
            if m:
                clips.append((cn_to_int(m.group(1)), fp))
                continue
            m = clip_pat_num.search(base)
            if m:
                clips.append((int(m.group(1)), fp))
    clips.sort()

    # 播报稿路径：根目录优先，找不到则退到 工作区/（2026-09-03起上游流水线把它挪进了工作区，和 today_entries/market_snapshot 一致）
    def resolve_in_workdir(fname):
        root_p = os.path.join(ddir, fname)
        if os.path.exists(root_p):
            return root_p
        wd_p = os.path.join(ddir, "工作区", fname)
        return wd_p if os.path.exists(wd_p) else root_p

    broadcast = parse_broadcast(resolve_in_workdir(cfg["broadcast_file"]))
    fcs = parse_first_comments(os.path.join(ddir, cfg["first_comment_file"]))
    tts = parse_toutiao(os.path.join(ddir, cfg["toutiao_file"]))
    te_path = os.path.join(ddir, cfg["today_entries_file"])
    today_entries = json.load(open(te_path, encoding='utf-8')) if os.path.exists(te_path) else []
    ms_path = os.path.join(ddir, cfg.get("market_snapshot_file", ""))
    market = json.load(open(ms_path, encoding='utf-8')) if ms_path and os.path.exists(ms_path) else {}

    # 锚定规则（2026-06-17 抽帧封面实测确立）：clip 文件名编号 == 首条评论序号（≠播报序号）。
    # 故首评按编号直取（还兼作"新闻身份锚"），播报正文/话题/头条再用该身份按内容匹配。
    fc_by_idx = {fc["idx"]: fc for fc in fcs}
    clip_objs = []
    for seq, fp in clips:
        pinned = fc_by_idx.get(seq)
        clip_objs.append({
            "clip_num": seq,                            # 文件名编号 = 首条评论序号
            "video": fp,
            "pinned_comment": pinned,                   # 置顶首评，按编号直取（零匹配）
            "news_id": (pinned or {}).get("head", ""),  # 新闻身份锚 → 去下面池子按内容匹配
        })

    out = {
        "show": show,
        "date": date,
        "clips_present": [s for s, _ in clips],
        "clips": clip_objs,
        "broadcast_items": broadcast,        # {播报序号: {broadcast_title, narration}} 全量，按内容匹配
        "toutiao": tts,                      # 头条图文标题池，按内容匹配
        "today_entries": today_entries,      # topic_tags 来源，按内容匹配
        "market_snapshot": market,
        "_anchor_rule": "clip_num == 首条评论序号（已验证 6/13 全中）；用 clip.news_id / pinned_comment "
                        "的新闻身份，去 broadcast_items / today_entries / toutiao 里【按内容】匹配，"
                        "取播报标题+正文 / 话题 / 民生标题。切忌把 clip_num 当播报序号——两者不一致。",
    }
    outdir = os.path.join(UPLOAD_ROOT, "物料", show, date)
    os.makedirs(outdir, exist_ok=True)
    outpath = os.path.join(outdir, "clips_raw.json")
    json.dump(out, open(outpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"✓ 解析完成: {len(clip_objs)} 条 clip，播报序号 {[s for s, _ in clips]}")
    print(f"  首评 {len(fcs)} 条 · 头条 {len(tts)} 条 · today_entries {len(today_entries)} 条 · 行情 {'有' if market else '无'}")
    print(f"  → {outpath}")


if __name__ == "__main__":
    main()
