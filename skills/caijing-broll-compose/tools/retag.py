#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retag.py — 库片段全量重审工具（2026-07-02 返工：治"小图猜内容瞎打标"）

审看原则：打标者看到的必须是【成片真正露出的中央可见带】的高清画面，
而不是 200px 缩略图。每片段一行 = 全帧(标红线=可见带边界) + 头/中/尾三帧中央带裁剪放大。

用法：
  python3 retag.py pages                 # 生成分页重审图 _review/_retag/page_NN.png + manifest.json
  python3 retag.py apply <decisions.json># 合并新标签进 library.json（含删除/同源家族归并）

decisions.json 格式（我逐页审看后写）：
{"clips": {"<md5前6位hash>": {
    "subjects":["熔金","坩埚"], "themes":["黄金","大宗","制造"],
    "attrs":{"shot":"特写","time":"室内","motion":"动","loc":"室内"},
    "desc":"坩埚金水浇铸进模具，火花四溅",
    "action":"keep|delete|recut", "recut":[in,out]  # recut时给源内新切点
}}}
scene_id 由 apply 自动归并：同源文件、时间相邻(≤1.5s 间隔)的片段 = 同一家族。
"""
import os, sys, json, subprocess, hashlib, re
from PIL import Image, ImageDraw, ImageFont

ROOT   = os.path.abspath(os.environ.get("BROLL_LIBRARY") or os.path.join(os.path.dirname(__file__), ".."))
CLIPS  = os.path.join(ROOT, "clips")
LIB    = os.path.join(ROOT, "library.json")
OUTDIR = os.path.join(ROOT, "_review", "_retag")

W, H = 1440, 2560
BAND_TOP, BAND_BOT = 700, 1860          # 成片中央可见带（compose.py 同参数）
FULL_W  = 170                            # 全帧列宽
CROP_W  = 400                            # 中央带裁剪列宽（可见带 1440x1160 → 400x322）
ROWS_PER_PAGE = 7

def run(cmd): return subprocess.run(cmd, capture_output=True, text=True)

def probe_dur(p):
    r = run(["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=nw=1:nk=1", p])
    try: return float(r.stdout.strip())
    except: return 0.0

def _font(sz):
    for p in ["/System/Library/Fonts/PingFang.ttc",
              "/System/Library/Fonts/Helvetica.ttc"]:
        try: return ImageFont.truetype(p, sz)
        except Exception: pass
    return ImageFont.load_default()

def grab(path, t, out):
    run(["ffmpeg","-y","-ss",f"{t:.2f}","-i",path,"-frames:v","1",out,"-loglevel","error"])
    return os.path.exists(out)

def clip_hash(c):
    m = re.search(r"_([0-9a-f]{6})\.mp4$", os.path.basename(c["file"]))
    return m.group(1) if m else hashlib.md5(c["file"].encode()).hexdigest()[:6]

def cmd_pages():
    lib = json.load(open(LIB))["clips"]
    os.makedirs(OUTDIR, exist_ok=True)
    tmp = os.path.join(OUTDIR, "_tmp"); os.makedirs(tmp, exist_ok=True)
    f_lab = _font(20); f_sm = _font(15)
    manifest = []
    rows = []
    for i, c in enumerate(lib, 1):
        p = os.path.join(ROOT, c["file"])
        if not os.path.exists(p): continue
        h = clip_hash(c); dur = probe_dur(p)
        frames = []
        ok = True
        for k, frac in enumerate((0.08, 0.5, 0.92)):
            fp = os.path.join(tmp, f"{h}_{k}.png")
            if not grab(p, max(0.0, dur*frac), fp): ok = False; break
            frames.append(fp)
        if not ok: continue
        # 全帧(中间帧) + 红线
        full = Image.open(frames[1]).convert("RGB").resize((FULL_W, int(FULL_W*H/W)))
        d = ImageDraw.Draw(full)
        y1 = int(BAND_TOP/H*full.height); y2 = int(BAND_BOT/H*full.height)
        for y in (y1, y2): d.line([(0,y),(full.width,y)], fill=(255,40,40), width=2)
        # 三帧中央带裁剪
        crops = []
        for fp in frames:
            im = Image.open(fp).convert("RGB")
            band = im.crop((0, BAND_TOP, W, BAND_BOT)).resize((CROP_W, int(CROP_W*(BAND_BOT-BAND_TOP)/W)))
            crops.append(band)
        row_h = max(full.height, crops[0].height) + 26
        row = Image.new("RGB", (FULL_W + 3*(CROP_W+6) + 18, row_h), (15,15,15))
        row.paste(full, (0, 0))
        x = FULL_W + 12
        for cr in crops: row.paste(cr, (x, 0)); x += CROP_W + 6
        dr = ImageDraw.Draw(row)
        lab = f"{i:03d}|{h}| {os.path.basename(c['file'])[:52]}  ({c.get('dur','?')}s)"
        dr.rectangle([0,row_h-24,row.width,row_h], fill=(0,0,0))
        dr.text((4,row_h-22), lab, fill=(255,230,0), font=f_lab)
        rows.append(row)
        manifest.append({"n":i,"hash":h,"file":c["file"],"dur":c.get("dur"),
                         "old_visual":c.get("visual"),"old_theme":c.get("theme")})
    # 分页
    pages = []
    for pi in range(0, len(rows), ROWS_PER_PAGE):
        chunk = rows[pi:pi+ROWS_PER_PAGE]
        pw = max(r.width for r in chunk); ph = sum(r.height+4 for r in chunk)
        page = Image.new("RGB", (pw, ph), (0,0,0)); y = 0
        for r in chunk: page.paste(r, (0,y)); y += r.height + 4
        out = os.path.join(OUTDIR, f"page_{pi//ROWS_PER_PAGE+1:02d}.png")
        page.save(out); pages.append(out)
    json.dump(manifest, open(os.path.join(OUTDIR,"manifest.json"),"w"),
              ensure_ascii=False, indent=1)
    print(f"重审页 {len(pages)} 页 / {len(rows)} 片段：")
    for p in pages: print(" ", p)

import glob as _glob
WD_ROOT = os.environ.get("BROLL_ARCHIVE", "")  # 可选：源视频归档盘（按主题分文件夹）
BASE_DIR = os.path.abspath(os.path.join(ROOT, ".."))
def resolve_source(c):
    """源可能已按主题归档到WD(改名 日期_原名.mp4)，多路径找回。"""
    cands = [c.get("source_abs"), c.get("source")]
    base = os.path.basename(c.get("source","") or "")
    if base:
        pats = [os.path.join(BASE_DIR,"*","PNG素材",base)]
        if WD_ROOT:
            pats = [os.path.join(WD_ROOT,"*",base), os.path.join(WD_ROOT,"*","*_"+base)] + pats
        for pat in pats:
            cands += sorted(_glob.glob(pat))
    for p in cands:
        if p and os.path.exists(p): return p
    return None

def cmd_apply(dec_path):
    dec = json.load(open(dec_path))["clips"]
    lib = json.load(open(LIB))
    kept, deleted, recut = 0, 0, 0
    out_clips = []
    for c in lib["clips"]:
        h = clip_hash(c)
        d = dec.get(h)
        if d is None:                      # 本批未审到，原样保留
            out_clips.append(c); continue
        act = d.get("action","keep")
        if act == "delete":
            p = os.path.join(ROOT, c["file"])
            if os.path.exists(p): os.remove(p)
            deleted += 1; continue
        if act == "recut" and d.get("recut"):
            a,b = d["recut"]
            src = resolve_source(c)
            if src:
                p = os.path.join(ROOT, c["file"])
                vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
                run(["ffmpeg","-y","-ss",str(a),"-to",str(b),"-i",src,"-vf",vf,"-an",
                     "-r","30","-c:v","libx264","-crf","20","-preset","medium",
                     "-pix_fmt","yuv420p",p,"-loglevel","error"])
                c["in"],c["out"],c["dur"] = a,b,round(b-a,2); recut += 1
        if d.get("trim"):                  # 直接裁库内成品片段(相对时间,无需回源)
            a,b = d["trim"]
            p = os.path.join(ROOT, c["file"]); tmpp = p+".tmp.mp4"
            run(["ffmpeg","-y","-ss",str(a),"-to",str(b),"-i",p,"-an","-r","30",
                 "-c:v","libx264","-crf","20","-preset","medium","-pix_fmt","yuv420p",
                 tmpp,"-loglevel","error"])
            if os.path.exists(tmpp): os.replace(tmpp, p); c["dur"]=round(b-a,2); recut += 1
        for k in ("subjects","themes","attrs","desc","blemish"):
            if k in d: c[k] = d[k]
        if d.get("scene"): c["scene_tag"] = d["scene"]     # 手动同景归组(跨时间/跨源)
        kept += 1; out_clips.append(c)
    # 同源家族 scene_id：手动 scene_tag 优先(跨源同景)；否则 同源文件+时间相邻(≤1.5s)
    by_src = {}
    for c in out_clips: by_src.setdefault(c.get("source","?"), []).append(c)
    for src, cs in by_src.items():
        cs.sort(key=lambda x: x.get("in", 0))
        fam = 0; prev_out = None
        base = hashlib.md5(src.encode()).hexdigest()[:4]
        for c in cs:
            if prev_out is None or c.get("in",0) - prev_out > 1.5: fam += 1
            c["scene_id"] = f"{base}-{fam}"
            prev_out = max(prev_out or 0, c.get("out", 0))
    for c in out_clips:
        if c.get("scene_tag"): c["scene_id"] = "tag-"+c["scene_tag"]
    lib["clips"] = out_clips
    json.dump(lib, open(LIB,"w"), ensure_ascii=False, indent=2)
    fams = len({c.get("scene_id") for c in out_clips})
    print(f"apply完成: 重打标{kept} 删除{deleted} 重切{recut} | 库{len(out_clips)}片段/{fams}个同源家族")

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("pages","apply"):
        print(__doc__); sys.exit(1)
    if sys.argv[1] == "pages": cmd_pages()
    else: cmd_apply(sys.argv[2])
