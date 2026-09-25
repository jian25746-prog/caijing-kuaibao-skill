#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compose_day.py — 全天批量出片（2026-07-02 v2：全天统一分配器，治"同一期素材重复"）

分配铁律：
  • 同一条 12s 内：6 段必须来自 6 个不同【同源家族 scene_id】
  • 全天 10 条之间：同一家族默认只出现一次（全天严格不重复）
  • 跨天冷却（2026-07-03 用户定）：3 天内用过的片段绝不复用（硬）；
    4-7 天内用过的排到最后、用到即记入报告（软）。台账 素材库/_usage_log.json 每次渲染自动记
  • 家族不足时的放宽顺序（每一步都记入报告）：
      ① 长片段拆不同 2 秒窗口（画面同源但内容不同段，只跨条使用）
      ② 通用干净池补
      ③ 实在不够才允许家族跨条二用（选已用次数最少者，绝不进同一条）
  • 题材匹配优先级：themes 多标签精确命中 → 通用池；绝不随机全库

用法：
  python3 compose_day.py <当天文件夹> [--plan selection.json] [--music x.mp3] [--out 目录]
  selection.json（可选，模型语义匹配产物）: {"01":{"topic":"美股","themes":["美股","华尔街"],"gap":"..."}}
  不给 plan 时用内置关键词分类器定题材。
"""
import os, sys, json, glob, re, random
sys.path.insert(0, os.path.dirname(__file__))
import compose as C

SEG = C.SEG_DUR; NSEG = C.NSEG

# ── 跨天冷却：3天硬禁用 / 7天软避让 ──────────────────────────────
USAGE_LOG = os.path.join(os.path.dirname(C.LIB), "_usage_log.json")
COOLDOWN_HARD, COOLDOWN_SOFT = 3, 7

def load_usage(cur_day):
    """读用片台账 → (hard, soft) 片名集合。cur_day=当天文件夹名，重渲同一天不自我封锁。"""
    import datetime
    hard, soft = set(), set()
    if not os.path.exists(USAGE_LOG): return hard, soft
    try: log = json.load(open(USAGE_LOG))
    except Exception: return hard, soft
    today = datetime.date.today()
    for day, ent in log.items():
        if day == cur_day: continue
        try: age = (today - datetime.date.fromisoformat(ent["date"])).days
        except Exception: continue
        if age < COOLDOWN_HARD:   hard.update(ent.get("clips", []))
        elif age < COOLDOWN_SOFT: soft.update(ent.get("clips", []))
    soft -= hard
    return hard, soft

def save_usage(cur_day, used_names):
    """渲染完成后登记当天用片；顺带清理 30 天前旧账。"""
    import datetime
    log = {}
    if os.path.exists(USAGE_LOG):
        try: log = json.load(open(USAGE_LOG))
        except Exception: log = {}
    today = datetime.date.today()
    log[cur_day] = {"date": today.isoformat(), "clips": sorted(set(used_names))}
    for day in list(log):
        try:
            if (today - datetime.date.fromisoformat(log[day]["date"])).days > 30: del log[day]
        except Exception: del log[day]
    json.dump(log, open(USAGE_LOG, "w"), ensure_ascii=False, indent=1)

THEME_RULES = [
 ("比特币", ["比特币","加密","稳定币","以太","crypto","btc","数字货币","区块链"]),
 ("黄金",   ["黄金","金价","白银","贵金属"]),
 ("大宗",   ["大宗","铜价","铜矿","锡","铁矿","矿业"]),
 ("电力",   ["电力","核电","核能","风电","光伏","太阳能","水电","电网","储能","耗电","用电","发电","燃气轮机"]),
 ("石油",   ["石油","原油","油价","wti","布伦特","霍尔木兹","opec","天然气","油轮"]),  # "能源"太宽:新能源车曾误判石油(2026-07-03)
 ("医药",   ["医药","制药","药企","药厂","仿制药","专利药","原研药","原料药","创新药","临床试验","新药研发","药品关税","生物制剂","慢阻肺","copd","阿斯利康","山德士","诺华制药","辉瑞","礼来"]),  # 2026-09-09新增:此前0覆盖,仿制药/新药故事误落DEFAULT_THEME=美股
 ("美联储", ["美联储","降息","加息","利率","鲍威尔","缩表","点阵图","货币政策","央行","沃什"]),
 ("量子",   ["量子","量子计算","量子比特","量子芯片","量子霸权","ionq"]),
 ("存储",   ["存储","内存","闪存","美光","镁光","闪迪","海力士","hbm","dram","nand","ssd","固态硬盘","长江存储"]),
 ("机器人", ["机器人","人形机器人","宇树","optimus","擎天柱","具身智能"]),
 ("电动车", ["电动车","新能源车","新能源汽车","比亚迪","蔚来","小鹏","理想汽车","充电桩","锂电"]),
 ("半导体", ["半导体","芯片","台积电","光刻","晶圆"]),
 ("科技",   ["ai","算力","人工智能","大模型","科技股","数据中心","英伟达","黄仁勋","眼镜","可穿戴","自动驾驶"]),
 ("航天",   ["航天","卫星","spacex","火箭","星链","星舰"]),
 ("日本",   ["日本","日元","东京","日股","日经","日银","套息"]),
 ("韩国",   ["韩国","首尔","kospi","韩元","三星","sk海力士","现代汽车","起亚","韩股"]),
 ("A股",    ["a股","创业板","上证","北交所","中国股市","沪深","陆家嘴","人民币","银行板块","上市银行","增持","市值管理"]),
 ("个股",   ["个股","财报","大疆","特斯拉","苹果","meta","谷歌","亚马逊","微软","裁员","ipo","航空"]),
 ("美股",   ["美股","美债","纳斯达克","道琼斯","标普","美元","华尔街","国债","收益率","泡沫","vix"]),
]
DEFAULT_THEME = "美股"
# 细分版块库存不足时的语义近邻兜底（优先于通用池）
THEME_FALLBACK = {"量子":"半导体","存储":"半导体","机器人":"科技","电动车":"个股"}
# 题材默认地域（通用兜底时 region 必须 ∈ {该地域, 中性}；plan 可按条覆盖 "region"）
THEME_REGION = {"A股":"中","日本":"日","美股":"美","美联储":"美","电动车":"中","韩国":"韩"}
# 故事文本地域推断（只用地方专词，防"雕像"式误判；计票取多数，平票→None交题材默认）
REGION_HINTS = [
 ("中", ["中国","人民币","人民银行","北京","上海","深圳","比亚迪","宇树","长征","神舟","国产","沪指","深成指","A股","港股","香港"]),
 ("美", ["美国","美联储","华尔街","纳斯达克","纽约","美股","白宫","国会","spacex","特斯拉","苹果","微软","谷歌","meta","openai","anthropic","英伟达","亚马逊","鲍威尔","沃勒","马斯克","库克"]),
 ("欧", ["欧洲","欧央行","欧元","拉加德","德国","法国","布鲁塞尔","欧盟"]),
 ("日", ["日本","日经","日元","东京","日银"]),
 ("韩", ["韩国","首尔","kospi","韩元","三星电子","sk海力士","现代汽车","起亚"]),
]
def infer_region(text):
    t = text.lower()
    score = {reg: sum(1 for w in words if w in t) for reg, words in REGION_HINTS}
    best = max(score.values())
    if best == 0: return None
    tops = [r for r,v in score.items() if v == best]
    return tops[0] if len(tops) == 1 else None
# 强标识素材解锁词：故事文本命中任一别名才允许使用该 identity 的素材
ENTITY_ALIASES = {
 "美联航":["美联航","航空"], "达美":["达美","航空"], "亚马逊":["亚马逊","amazon"],
 "Adobe":["adobe"], "伯克希尔":["伯克希尔","巴菲特","阿贝尔"], "阿贝尔":["阿贝尔","巴菲特","伯克希尔"],
 "贝莱德":["贝莱德","blackrock"], "英伟达":["英伟达","nvidia","黄仁勋","gtc"],
 "Marvell":["marvell","迈威尔"], "比亚迪":["比亚迪","byd","电动车","新能源车"],
 "宇树":["宇树","人形机器人","机器人"], "SpaceX":["spacex","星链","星舰","火箭","航天"],
 "小鹏":["小鹏","xpeng","何小鹏","iron"],
 "大疆":["大疆","dji"], "特朗普":["特朗普"], "沃什":["沃什","美联储主席"],
 "黄仁勋":["黄仁勋","英伟达","nvidia"], "谷歌":["谷歌","google","量子"],
 "IBM":["ibm","量子"], "本源量子":["本源","量子"], "九章":["九章","量子","光量子"],
 "祖冲之":["祖冲之","量子","超导量子"], "蒂芙尼":["蒂芙尼","tiffany","奢侈品"],
 "苹果":["苹果","apple","iphone","库克"], "库克":["库克","苹果","apple","iphone"],
 "NYSE":["纽交所","nyse"],  # "交易所"太宽,会命中任何提到"期货交易所/证券交易所"的中国本土故事(2026-08-28发现,姜蒜电子盘条误判)
 "Meta":["meta","扎克伯格","facebook","instagram","元宇宙"],
 "美国航空":["美国航空","航空"], "沃尔玛":["沃尔玛","walmart"], "Target":["target","塔吉特"],
 "航空":["航空","民航","波音","空客","机票","航班"],
 "特斯拉":["特斯拉","tesla","马斯克","fsd","cybertruck","model y","model 3"],
 "马斯克":["马斯克","musk","特斯拉","spacex"],
 "微软":["微软","microsoft","windows","coreweave"],
 "OpenAI":["openai","chatgpt","奥特曼","altman"],  # 裸"gpt"太宽,会命中他家模型名子串(如AndesGPT)误判,2026-07-16已剔除
 "Anthropic":["anthropic","claude","克劳德"],
 "沃勒":["沃勒","waller"],
 "库克":["库克","cook","苹果"],
 "Kalshi":["kalshi","预测市场","polymarket","博彩合约"],
 "三星":["三星","samsung","三星电子"],
 "鲍威尔":["鲍威尔","powell"],
 "拉加德":["拉加德","lagarde"],
 "中国银行":["中国银行","bank of china","boc"],
 "茅台":["茅台","moutai"],
 "AMD":["amd","锐龙","ryzen","苏姿丰","lisa su"],
 "阿里巴巴":["阿里","阿里巴巴","alibaba","千问","qwen","支付宝","天猫","淘宝","菜鸟"],
 "美团":["美团","meituan"],
 "耐克":["耐克","nike"],
 "Citadel":["citadel","城堡投资","城堡证券"],
 "摩根大通":["摩根大通","摩根资管","jpmorgan","jp morgan","大卫·凯利"],
 "百度":["百度","baidu"],
 "博通":["博通","broadcom"],
 "阿斯利康":["阿斯利康","astrazeneca"],
}
# 实体哨兵：故事点名了这些实体但库里没有对应素材时 → 报警登记缺口
ENTITY_WATCH = ["特斯拉","meta","微软","谷歌","苹果","openai","anthropic","英伟达","亚马逊","比亚迪",
                "鲍威尔","拉加德","马斯克","欧央行","欧洲央行","台积电","三星","美光","软银","甲骨文","oracle",
                "沃勒","库克","claude","chatgpt","美团","饿了么","哈马克","贝森特",
                "deepseek","中芯国际","中芯","华虹","思科","cisco","京东","通威","大全能源","新特能源",
                "amd","博通","broadcom","阿波罗","apollo","黑石","blackstone","阿里","阿里巴巴","alibaba",
                "千问","qwen","茅台","moutai","百达","pictet","长鑫科技","长鑫","cxmt","qts","百度","baidu",
                "阿斯利康","astrazeneca","山德士","sandoz"]

def extract_entities(text):
    t=text.lower()
    ents=set()
    for ident,aliases in ENTITY_ALIASES.items():
        if any(a.lower() in t for a in aliases): ents.add(ident)
    watch_hits=[w for w in ENTITY_WATCH if w in t]
    return ents, watch_hits

def classify(text):
    t = text.lower()
    best, bestn = DEFAULT_THEME, 0
    for theme, kws in THEME_RULES:
        n = sum(t.count(k) for k in kws)
        if n > bestn: best, bestn = theme, n
    return best

def slice_text(s):
    parts = [s.get("title_banner","") or ""]
    parts += s.get("hook",[]) if isinstance(s.get("hook"),list) else [s.get("hook","")]
    parts += [s.get("narration","") or ""]
    parts += s.get("bottom",[]) if isinstance(s.get("bottom"),list) else []
    return " ".join(parts)

def load_slices(day):
    for p in [os.path.join(day,"工作区","caijing_slices.json"),
              os.path.join(day,"caijing_slices.json")]:
        if os.path.exists(p): return json.load(open(p))
    return None

# ---------------- 全天分配器 ----------------
def clip_themes(c):
    """兼容新旧schema：themes多标签优先，退回theme单值。"""
    th = c.get("themes")
    if th: return set(th)
    return {c.get("theme","通用")}

def build_pool(lib, hard=frozenset(), soft=frozenset(), hand_used=frozenset()):
    """家族 -> {clips, themes, generic, tier, windows:[(clip,ws,tier)]}
    hard=3天内用过(片名) → 窗口直接不进池；soft=4-7天内用过 → tier=1 排到本家族最后。
    hand_used=本日已被手选占用的具体片名 → 窗口不进池(家族仍可复用其它clip,但这个具体clip绝不重复出现)。"""
    fams = {}
    for c in lib:
        sid = c.get("scene_id") or os.path.basename(c["file"])   # 未归并时每片段自成家族
        f = fams.setdefault(sid, {"clips":[], "themes":set(), "generic":False})
        f["clips"].append(c)
        f["themes"] |= clip_themes(c)
        f["generic"] = f["generic"] or bool(c.get("generic"))
    for f in fams.values():
        wins = []
        for c in f["clips"]:
            base = os.path.basename(c["file"])
            if base in hard or base in hand_used: continue  # ⛔ 3天冷却/本日已手选：整条片段出池
            tier = 1 if base in soft else 0
            d = float(c.get("dur", SEG))
            n = max(1, min(4, int(d // SEG)))
            for k in range(n): wins.append((c, round(k*SEG,2), tier))
        random.shuffle(wins)
        wins.sort(key=lambda w: w[2])                     # 新鲜窗口在前(稳定排序保留洗牌)
        f["windows"] = wins
        f["tier"] = min((w[2] for w in wins), default=2)  # 2=全家族被硬冷却清空
    return fams

def allocate(day_wants, fams, reserved=frozenset(), reserved_clips=frozenset()):
    """day_wants: [(idx, theme, region, ents, watch)]  →  {idx: [seg×6] | None(熔断)}, notes, gaps
    匹配链：①实体命中 → ②同题材(排异牌+地域) → ②b近邻兜底 → ③中性通用(地域) → 复用放宽 → 熔断
    reserved: 已被人工直选片单占用的家族id集合 → 预置为已用,分配器不再抢(避免手选与自动跨条撞同一片)
    reserved_clips: 手选片具体文件名集合 → 复用放宽步骤也绝不碰(2026-07-18教训:仅家族预留时,
    复用步骤仍可能把手选同一支clip再分给自动条,造成同clip同天两条成片)"""
    notes, gaps = [], []
    fam_used = {fid: 1 for fid in reserved}   # 手选片家族预留：非复用步骤直接跳过
    def match_size(t):
        return sum(1 for f in fams.values() if t in f["themes"])
    order = sorted(day_wants, key=lambda x: match_size(x[1]))
    result = {}
    fam_win_used = {}
    clip_used_today = set(reserved_clips)  # 具体clip文件名级别去重：同一clip的不同时间窗口也不能跨条重复；手选片名预先种入(2026-07-18教训:否则复用步骤会把手选同一支clip再分给自动条)
    for idx, theme, region, ents, watch in order:
        segs, used_here = [], set()
        def ok_ident(c):
            # 挂着别家标识的素材，只有故事点名(别名命中)才可用
            i = c.get("identity")
            return (not i) or (i in ents)
        def ok_region(c):
            if not region: return True
            r = c.get("region","中性")
            return r == region or r == "中性"
        def take(fam_ids, pred, allow_reuse=False, tag="", cap=NSEG):
            nonlocal segs
            pool = [(fid, fams[fid]) for fid in fam_ids if fid not in used_here]
            if not allow_reuse:
                pool = [(fid,f) for fid,f in pool if fam_used.get(fid,0)==0]
            # 排序：已用次数少者优先，其次新鲜家族(tier=0)先于7天软冷却家族(tier=1)
            pool.sort(key=lambda x: (fam_used.get(x[0],0), x[1].get("tier",0)))
            for fid, f in pool:
                if len(segs) >= min(cap, NSEG): return
                used = fam_win_used.setdefault(fid, set())
                pick = None
                for wi,(clip,ws,tier) in enumerate(f["windows"]):
                    if wi in used: continue
                    base = os.path.basename(clip["file"])
                    if base in clip_used_today: continue   # 同一具体clip(不同时间窗口)当天只许出现一次
                    if not (pred(clip) and ok_ident(clip) and ok_region(clip)): continue
                    pick = (wi, clip, ws, tier); break
                if pick is None: continue
                wi, clip, ws, tier = pick
                used.add(wi)
                clip_used_today.add(os.path.basename(clip["file"]))
                fam_used[fid] = fam_used.get(fid,0) + 1
                used_here.add(fid)
                d = dict(clip); d["ws"] = ws
                segs.append(d)
                if tier == 1: notes.append(f"{idx:02d} {theme}: 跨天复用放宽4-7天({os.path.basename(clip['file'])[:24]})")
                if tag: notes.append(f"{idx:02d} {theme}: {tag}({os.path.basename(clip['file'])[:24]})")
        all_ids = list(fams.keys())
        exact   = [fid for fid,f in fams.items() if theme in f["themes"]]
        generic = [fid for fid,f in fams.items() if f["generic"]]
        p_ent   = lambda c: c.get("identity") and c["identity"] in ents
        p_theme = lambda c: theme in clip_themes(c)
        p_gen   = lambda c: bool(c.get("generic"))
        fb = THEME_FALLBACK.get(theme)
        if ents: take(all_ids, p_ent, cap=3)                   # ⓪ 点名实体素材优先(≤3段,留位给场景多样性)
        n_ent = len(segs)
        take(exact, p_theme)                                   # ① 同题材(排异牌+地域)
        if fb and len(segs) < NSEG:
            fb_ids = [fid for fid,f in fams.items() if fb in f["themes"]]
            take(fb_ids, lambda c: fb in clip_themes(c))       # ①b 语义近邻兜底
        if len(segs) < NSEG: take(generic, p_gen)              # ② 中性通用(地域过滤)
        if len(segs) < NSEG: take(exact,   p_theme, True, "家族跨条复用")
        if len(segs) < NSEG: take(generic, p_gen,   True, "通用跨条复用")
        # 实体缺口报警：点名了哨兵实体但一段实体素材都没配上
        if watch and n_ent == 0:
            gaps.append(f"{idx:02d} {theme}: 🔔实体缺口 故事点名[{'/'.join(watch)}]，库内无对应素材，已用中性顶替")
        if len(segs) < NSEG:                                   # ⛔ 熔断
            gaps.append(f"{idx:02d} {theme}: ⛔熔断不渲染 合格素材仅{len(segs)}/6段"
                        + (f"（地域={region}）" if region else "") + f"，缺口：{'/'.join(watch) if watch else theme+'题材素材'}")
            result[idx] = None
            continue
        result[idx] = segs
    return result, notes, gaps

def _check_duration(out_path, segs):
    """渲染后硬校验：视频流时长必须=C.TOTAL(12.0s)，段数必须=NSEG。
    2026-08-15教训：compose()不校验plan_clips数量/时长，手选片凑不够6段×2s会静默产出短视频
    (曾出现5段+1个1.5s短clip→只有10s成片，QC肉眼抽帧看不出来，靠ffprobe算帧数才发现)。"""
    import subprocess
    msgs = []
    if len(segs) != NSEG:
        msgs.append(f"背景段数={len(segs)}≠{NSEG}")
    try:
        r = subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
                             "-show_entries","stream=duration","-of","csv=p=0", out_path],
                            capture_output=True, text=True, timeout=15)
        dur = float(r.stdout.strip())
        if abs(dur - C.TOTAL) > 0.05:
            msgs.append(f"视频流时长={dur:.2f}s≠{C.TOTAL}s")
    except Exception as e:
        msgs.append(f"ffprobe校验失败:{e}")
    return (len(msgs)==0), "; ".join(msgs)

def main():
    day = sys.argv[1].rstrip("/")
    def opt(n,d=None): return sys.argv[sys.argv.index(n)+1] if n in sys.argv else d
    png_dir = os.path.join(day,"PNG素材")
    out_dir = opt("--out", os.path.join(day,"成片")); os.makedirs(out_dir,exist_ok=True)
    music_arg = opt("--music")
    slices = load_slices(day)
    if slices is None: print("找不到 caijing_slices.json"); sys.exit(1)
    plan = json.load(open(opt("--plan"))) if opt("--plan") else {}

    lib = json.load(open(C.LIB))["clips"]
    day_name = os.path.basename(day)
    random.seed(day_name)   # 按天播种：daily_brief核过的分配与正式渲染逐帧一致
    hard, soft = load_usage(day_name)
    hook_idx = sorted({int(re.search(r"hook_(\d+)_",os.path.basename(f)).group(1))
                       for f in glob.glob(os.path.join(png_dir,"hook_*_*.png"))})
    only_arg = opt("--only")   # 补渲染指定条目(如"06,09"),跳过当天已成功的其余条,避免重跑扰动
    already_today_clips = set()
    if only_arg:
        wanted = {int(x) for x in only_arg.split(",")}
        hook_idx = [i for i in hook_idx if i in wanted]
        if os.path.exists(USAGE_LOG):
            try:
                today_log = json.load(open(USAGE_LOG)).get(day_name, {})
                already_today_clips = set(today_log.get("clips", []))
            except Exception: pass
    day_wants, plan_gaps, hand_picked = [], [], {}
    for idx in hook_idx:
        s = slices[idx-1] if idx-1 < len(slices) else {}
        key = f"{idx:02d}"; p = plan.get(key) or {}
        text = slice_text(s)
        theme = p.get("topic") or classify(text)
        # 地域优先级：plan人工指定 > 故事文本专词计票 > 题材默认
        region = p["region"] if "region" in p else (infer_region(text) or THEME_REGION.get(theme))
        ents, watch = extract_entities(text + " " + " ".join(p.get("entities",[])))
        if p.get("gap"): plan_gaps.append(f"{key} {theme}: {p['gap']}")
        if p.get("clips"):                                  # 模型直选片单: ["hash6:ws", ...]
            segs=[]
            for spec in p["clips"]:
                h, _, ws = spec.partition(":")
                cand=[c for c in lib if h in c["file"]]
                if cand:
                    base=os.path.basename(cand[0]["file"])
                    if base in hard:
                        plan_gaps.append(f"{key} {theme}: ⚠️ 手选片{base[:24]}在3天冷却期内(仍按人工指定使用)")
                    d=dict(cand[0]); d["ws"]=float(ws or 0); segs.append(d)
            hand_picked[idx]=(theme,segs)
        day_wants.append((idx, theme, region, ents, watch))

    # 手选片：具体片名彻底出池(build_pool前置排除,防止自动分配复用阶段选中同一clip)；
    # 家族级别另预留,防止自动分配主动抢占手选打算用的家族
    def _fam(c): return c.get("scene_id") or os.path.basename(c["file"])
    hand_used = {os.path.basename(s["file"]) for _, segs in hand_picked.values() for s in segs}
    reserved = {_fam(s) for _, segs in hand_picked.values() for s in segs}
    reserved_clips = {os.path.basename(s["file"]) for _, segs in hand_picked.values() for s in segs}
    fams = build_pool(lib, hard | already_today_clips, soft, hand_used)  # --only补渲染:当天已交付的片子彻底出池,防止和已完成成片撞同一clip
    alloc, notes, gaps = allocate([w for w in day_wants if w[0] not in hand_picked], fams, reserved, reserved_clips)
    verbose = "--verbose" in sys.argv   # 默认紧凑输出省token；全量明细进 _分配报告.txt 不回显
    import io, contextlib
    print(f"📅 {day_name}  {len(day_wants)}条 | 库{len(lib)} | 冷却:禁{len(hard)}/避{len(soft)} | 手选{len(hand_picked)}条")
    done=0; fused=[]; used_names=[]; ledger=[]
    for idx, theme, region, ents, watch in day_wants:
        if idx in hand_picked:
            theme, segs = hand_picked[idx]
        else:
            segs = alloc.get(idx)
        if segs is None:
            print(f"[{idx:02d}] {theme}  ⛔ 熔断不渲染(缺素材,详见报告)")
            fused.append(idx); continue
        music = C.pick_music(music_arg)
        out = os.path.join(out_dir, f"成片_{idx:02d}_{theme}.mp4")
        if verbose:
            print(f"[{idx:02d}] {theme}  6段: " + " | ".join(os.path.basename(s['file'])[:16]+f"@{s['ws']}" for s in segs))
        try:
            if verbose:
                C.compose(png_dir, idx, theme, music, out, plan_clips=segs)
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    C.compose(png_dir, idx, theme, music, out, plan_clips=segs)
            done+=1
            names=[os.path.basename(s['file']) for s in segs]
            used_names += names
            ledger.append(f"{idx:02d} {theme}: " + " | ".join(names))
            dur_ok, dur_msg = _check_duration(out, segs)
            if not dur_ok: print(f"⚠️ [{idx:02d}]{theme} 时长校验异常: {dur_msg}")
            if not verbose: print(f"✅ [{idx:02d}]{theme} {os.path.getsize(out)/1e6:.1f}MB" + ("" if dur_ok else " ⚠️时长异常见上"))
        except SystemExit as e: print(f"   ⚠️ 跳过[{idx:02d}]: {e}")
        except Exception as e:  print(f"   ❌ [{idx:02d}] {e}")
    print(f"🎉 完成 {done}/{len(day_wants)} 条" + (f"，⛔熔断{len(fused)}条" if fused else "") + f" → {out_dir}")
    if used_names:
        save_usage(day_name, list(set(used_names) | already_today_clips))  # --only补渲染:与当天已有台账合并,不覆盖
        print(f"📒 台账已记 {len(set(used_names) | already_today_clips)} 片")
    rep=[]
    if gaps:      rep += ["【缺口报警(熔断/实体)】"] + gaps
    if plan_gaps: rep += ["【语义缺口(plan)】"] + plan_gaps
    if notes:     rep += ["【分配放宽记录】"] + notes
    if ledger:    rep += ["【本日用片台账(跨天冷却依据)】"] + ledger
    if rep:
        rp=os.path.join(out_dir,"_分配报告.txt")
        open(rp,"w").write("\n".join(rep)+"\n")
        print(f"📝 报告({len(gaps)}报警/{len(notes)}放宽): "+rp)
        for g in gaps: print("   ⚠️ "+g)       # 报警必须回显；语义缺口/放宽/台账只落盘
        if verbose:
            for line in rep: print("   "+line)
    if gaps:  # 缺口滚动登记，指导补素材
        gl = os.path.join(os.path.dirname(C.LIB), "_review", "素材缺口清单.md")
        import datetime
        os.makedirs(os.path.dirname(gl), exist_ok=True)
        with open(gl, "a", encoding="utf-8") as f:
            f.write(f"\n## {os.path.basename(day)} (登记于{datetime.date.today()})\n")
            for g in gaps: f.write(f"- {g}\n")
        print("📋 缺口已登记: "+gl)

if __name__=="__main__":
    if len(sys.argv)<2: print(__doc__); sys.exit(1)
    main()
