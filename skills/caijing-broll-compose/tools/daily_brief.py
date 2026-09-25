#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_brief.py — 日更选片一屏简报（省token版，2026-07-17）
把此前分散在多次调用里的【故事概览+冷却状态+干跑分配+实体命中+放宽统计+全天clip重复检测】
压缩成一次调用、一屏紧凑输出。人工只需扫每条的6段desc找"跳题"异常。

用法:
  python3 daily_brief.py <当天文件夹> [--plan selection.json] [--narr N,M]
  --narr 1,4  只打印第1/4条的旁白全文(默认只打hook首行,分类存疑时用)

输出标记: ✋手选 🤖自动 | [实体名]desc | 🟡=4-7天软避复用 ♻️×n=该条放宽次数
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))
import compose_day as cd

def main():
    day = sys.argv[1].rstrip('/')
    def opt(n, d=None): return sys.argv[sys.argv.index(n)+1] if n in sys.argv else d
    plan_p = opt('--plan')
    plan = json.load(open(plan_p)) if plan_p and os.path.exists(plan_p) else {}
    narr_idx = {int(x) for x in opt('--narr','').split(',') if x.strip().isdigit()}
    lib = json.load(open(cd.C.LIB))['clips']
    slices = cd.load_slices(day)
    if slices is None: print("找不到 caijing_slices.json"); sys.exit(1)
    day_name = os.path.basename(day)
    cd.random.seed(day_name)   # 与compose_day同种子：简报所见=渲染所得
    hard, soft = cd.load_usage(day_name)

    day_wants, hand_picked = [], {}
    for idx in range(1, len(slices)+1):
        s = slices[idx-1]; key = f"{idx:02d}"; p = plan.get(key) or {}
        text = cd.slice_text(s)
        theme = p.get('topic') or cd.classify(text)
        region = p['region'] if 'region' in p else (cd.infer_region(text) or cd.THEME_REGION.get(theme))
        ents, watch = cd.extract_entities(text + ' ' + ' '.join(p.get('entities', [])))
        if p.get('clips'):
            segs = []
            for spec in p['clips']:
                h, _, ws = spec.partition(':')
                cand = [c for c in lib if h in c['file']]
                if cand:
                    d = dict(cand[0]); d['ws'] = float(ws or 0); segs.append(d)
            hand_picked[idx] = (theme, segs)
        day_wants.append((idx, theme, region, ents, watch, s))

    hand_used = {os.path.basename(c['file']) for _, segs in hand_picked.values() for c in segs}
    reserved = {(c.get('scene_id') or os.path.basename(c['file'])) for _, segs in hand_picked.values() for c in segs}
    fams = cd.build_pool(lib, hard, soft, hand_used)
    wants4 = [(i, t, r, e, w) for i, t, r, e, w, _ in day_wants]
    result, notes, gaps = cd.allocate([w for w in wants4 if w[0] not in hand_picked], fams, reserved)

    print(f"📅 {day_name} {len(day_wants)}条 | 冷却:硬禁{len(hard)}/软避{len(soft)} | 手选{len(hand_picked)}条 | 库{len(lib)}")
    relax = {}
    for n in notes:
        try: i = int(n[:2]); relax[i] = relax.get(i, 0) + 1
        except ValueError: pass
    allbase = {}
    for idx, theme, region, ents, watch, s in day_wants:
        hook = (s.get('hook') or [''])[0]
        mark = '✋' if idx in hand_picked else '🤖'
        segs = hand_picked[idx][1] if idx in hand_picked else result.get(idx)
        line2 = f" ents={sorted(ents)}" if ents else ""
        print(f"[{idx:02d}]{mark}{theme}/{region or '-'} {hook[:24]}{line2}")
        if idx in narr_idx: print("    NARR:", s.get('narration','')[:200])
        if segs is None:
            print("    ⛔ 熔断"); continue
        parts = []
        for d in segs:
            cool = '🟡' if os.path.basename(d['file']) in soft else ''
            ident = d.get('identity')
            parts.append(f"{'['+ident+']' if ident else ''}{d.get('desc','')[:11]}{cool}")
        rx = f" ♻️×{relax[idx]}" if relax.get(idx) else ""
        print("    " + "|".join(parts) + rx)
        allbase[idx] = [os.path.basename(d['file']) for d in segs]
    seen = {}
    for idx, names in allbase.items():
        for n in names: seen.setdefault(n, []).append(idx)
    dups = {n[:26]: v for n, v in seen.items() if len(v) > 1}
    for g in gaps: print("⚠️", g)
    print("clip重复: " + (str(dups) if dups else "无"))

if __name__ == '__main__':
    main()
