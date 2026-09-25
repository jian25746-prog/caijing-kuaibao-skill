#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prep_broll.py — 7点财经 素材库 清洗+打标 工具（为自动合成成片准备视频元素）

成片合成规格（已端到端验证）：
  背景视频 cover缩放到 1440x2560  +  hook卡(顶部~22%)  +  narration/bottom卡(下三分之一)
  => 故"必须干净"的是【画面中央带 25%~60% 高度】；顶/底会被卡片遮住，可放宽。

两阶段用法：
  1) 扫描分镜 + 抽帧给人/AI打标：
       python3 prep_broll.py scan <视频或文件夹> [--date 20260629]
     产物：素材库/_review/<date>/  下每个源一张联系表(标了镜头号) + pending.json

  2) 按打标结果导出到库（标准化1440x2560、精确切、可裁UI带、写总清单）：
       python3 prep_broll.py build <decisions.json>

依赖：系统 ffmpeg / ffprobe。
"""
import os, sys, re, json, subprocess, hashlib, datetime
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.abspath(os.environ.get("BROLL_LIBRARY") or os.path.join(os.path.dirname(__file__), ".."))
REVIEW = os.path.join(ROOT, "_review")
CLIPS  = os.path.join(ROOT, "clips")
LIB    = os.path.join(ROOT, "library.json")

TARGET_W, TARGET_H = 1440, 2560
SCENE_THRESH = 0.22      # 场景切变阈值(调灵敏以分开广告/弱切点)
MIN_SHOT     = 1.2       # 短于此秒数的镜头丢弃
MAX_SHOT     = 8.0       # 长于此的镜头再均匀细分为<=此长的原子片
THUMB_W      = 220
SHEET_COLS   = 5         # 联系表列数(少列=大图=序号清晰)

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def probe_dur(path):
    r = run(["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=nw=1:nk=1", path])
    try: return float(r.stdout.strip())
    except: return 0.0

def scene_cuts(path):
    r = run(["ffmpeg","-i",path,"-filter:v",
             f"select='gt(scene,{SCENE_THRESH})',showinfo","-f","null","-"])
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    return sorted(set(round(t,2) for t in times))

def build_shots(path):
    """返回原子镜头列表 [(in,out)]，已按 MIN/MAX 规整。"""
    dur = probe_dur(path)
    cuts = [0.0] + [c for c in scene_cuts(path) if 0 < c < dur] + [dur]
    cuts = sorted(set(round(c,2) for c in cuts))
    raw = []
    for a,b in zip(cuts, cuts[1:]):
        if b-a < MIN_SHOT:  # 丢弃过短/微闪
            continue
        raw.append((a,b))
    # 细分过长镜头
    shots=[]
    for a,b in raw:
        seg=b-a
        if seg<=MAX_SHOT:
            shots.append((a,b)); continue
        n=int(seg//MAX_SHOT)+1
        step=seg/n
        for k in range(n):
            shots.append((round(a+k*step,2), round(a+(k+1)*step,2)))
    return dur, shots

def thumb(path, t, out):
    run(["ffmpeg","-y","-ss",str(t),"-i",path,"-frames:v","1",
         "-vf",f"scale={THUMB_W}:-1","-q:v","3",out,"-loglevel","error"])

def _font(sz):
    for p in ["/System/Library/Fonts/Helvetica.ttc",
              "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/Library/Fonts/Arial.ttf"]:
        try: return ImageFont.truetype(p, sz)
        except Exception: pass
    return ImageFont.load_default()

def make_sheet(thumbs, labels, out, cols=SHEET_COLS, pad=6, bg=(18,18,18)):
    """PIL 拼联系表：每格左上角烫大号序号、底部标时间段。序号=sid镜头号，杜绝数错。"""
    ims=[Image.open(t).convert("RGB") for t in thumbs]
    if not ims: return
    w=max(i.width for i in ims); h=max(i.height for i in ims)
    rows=(len(ims)+cols-1)//cols
    W=cols*w+(cols+1)*pad; H=rows*h+(rows+1)*pad
    sheet=Image.new("RGB",(W,H),bg); dr=ImageDraw.Draw(sheet)
    f_big=_font(max(26,h//7)); f_sm=_font(max(13,h//18))
    for k,(im,lab) in enumerate(zip(ims,labels)):
        r,c=divmod(k,cols); x=pad+c*(w+pad); y=pad+r*(h+pad)
        sheet.paste(im,(x,y))
        idx=lab.split()[0]
        bb=f_big.getbbox(idx)
        dr.rectangle([x,y,x+bb[2]+14,y+bb[3]+10],fill=(0,0,0))
        dr.text((x+6,y+2),idx,fill=(255,225,0),font=f_big)
        tb=f_sm.getbbox(lab)
        dr.rectangle([x,y+h-tb[3]-8,x+w,y+h],fill=(0,0,0))
        dr.text((x+4,y+h-tb[3]-6),lab,fill=(170,255,170),font=f_sm)
    sheet.save(out)

def cmd_scan(target, date):
    files=[]
    if os.path.isdir(target):
        for fn in sorted(os.listdir(target)):
            if fn.startswith("._"):          # 跳过 macOS AppleDouble 影子文件
                continue
            if fn.lower().endswith((".mp4",".mov",".m4v",".avi",".mkv")):
                files.append(os.path.join(target,fn))
    else:
        files=[target]
    rdir=os.path.join(REVIEW,date); os.makedirs(rdir,exist_ok=True)
    tdir=os.path.join(rdir,"thumbs"); os.makedirs(tdir,exist_ok=True)
    pending=[]
    for f in files:
        base=os.path.splitext(os.path.basename(f))[0]
        dur,shots=build_shots(f)
        print(f"[{base}] {dur:.1f}s -> {len(shots)} 原子镜头")
        thumbs=[]; labels=[]
        for i,(a,b) in enumerate(shots,1):
            sid=f"{base}__{i:02d}"
            tp=os.path.join(tdir,f"{sid}.jpg")
            thumb(f,(a+b)/2,tp)
            thumbs.append(tp); labels.append(f"{i:02d} {a:.0f}-{b:.0f}s")
            pending.append({"sid":sid,"source":os.path.abspath(f),
                            "in":a,"out":b,"dur":round(b-a,2)})
        # PIL 拼带序号的联系表
        sheet=os.path.join(rdir,f"sheet_{base}.png")
        if thumbs:
            make_sheet(thumbs, labels, sheet)
    pj=os.path.join(rdir,"pending.json")
    json.dump(pending,open(pj,"w"),ensure_ascii=False,indent=2)
    print(f"\n联系表与 pending.json 已写入: {rdir}")
    print(f"镜头总数: {len(pending)}")

def _load_lib():
    if os.path.exists(LIB):
        return json.load(open(LIB))
    return {"clips":[]}

def cmd_build(decisions_path):
    dec=json.load(open(decisions_path))
    lib=_load_lib()
    date=dec.get("date",datetime.date.today().strftime("%Y%m%d"))
    kept=0
    for it in dec["items"]:
        if not it.get("keep"): continue
        src=it["source"]; a=it["in"]; b=it["out"]
        topic=it.get("topic","misc"); visual=it.get("visual","clip")
        cat=it.get("category",visual)
        crop=it.get("crop")  # [top,bottom,left,right] 像素，基于源分辨率，可空
        outdir=os.path.join(CLIPS,cat); os.makedirs(outdir,exist_ok=True)
        h=hashlib.md5(f"{src}{a}{b}".encode()).hexdigest()[:6]
        dur=round(b-a,2)
        name=f"{date}_{topic}_{visual}_{dur:.0f}s_{h}.mp4"
        out=os.path.join(outdir,name)
        # 滤镜：可选裁UI带 -> cover缩放铺满 1440x2560
        vf=[]
        if crop:
            t,bot,l,r=crop
            vf.append(f"crop=in_w-{l}-{r}:in_h-{t}-{bot}:{l}:{t}")
        vf.append(f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase")
        vf.append(f"crop={TARGET_W}:{TARGET_H}")
        vf.append("setsar=1")
        cmd=["ffmpeg","-y","-ss",str(a),"-to",str(b),"-i",src,
             "-vf",",".join(vf),"-an","-r","30",
             "-c:v","libx264","-crf","20","-preset","medium",
             "-pix_fmt","yuv420p",out,"-loglevel","error"]
        r=run(cmd)
        if r.returncode!=0:
            print(f"  ❌ {name}: {r.stderr[-300:]}"); continue
        entry={
            "file":os.path.relpath(out,ROOT),"date":date,
            "topic":topic,"visual":visual,"category":cat,
            "theme":it.get("theme",topic),"generic":it.get("generic",False),
            "dur":dur,"source":os.path.basename(src),
            "in":a,"out":b,"crop":crop,"note":it.get("note","")}
        for k in ("subjects","themes","attrs","desc","blemish"):
            if k in it: entry[k]=it[k]
        if it.get("scene"): entry["scene_tag"]=it["scene"]
        lib["clips"].append(entry)
        kept+=1
        print(f"  ✅ {cat}/{name}")
    json.dump(lib,open(LIB,"w"),ensure_ascii=False,indent=2)
    print(f"\n入库 {kept} 个片段；总库现有 {len(lib['clips'])} 个。清单: {LIB}")

BAND_TOP_T, BAND_BOT_T = 700, 1860   # 成片可见带(目标坐标)

def src_band(w, h):
    """cover裁切到1440x2560后,可见带映射回源图的y范围"""
    sc = max(TARGET_W/w, TARGET_H/h)
    off = (h*sc - TARGET_H) / 2
    return (BAND_TOP_T+off)/sc, (BAND_BOT_T+off)/sc

def cmd_zoom(theme, spec):
    """高清候选审看：spec='文件名子串:1,2,5;另一文件:3'。
    每镜头一行 = 全帧(中间帧,标可见带红线) + 头/中/尾三帧中央带裁剪放大。"""
    rdir=os.path.join(REVIEW,theme)
    pend=json.load(open(os.path.join(rdir,"pending.json")))
    want=[]
    for part in spec.split(";"):
        if ":" not in part: continue
        sub, ids = part.split(":",1)
        idset={int(x) for x in ids.split(",") if x.strip()}
        for x in pend:
            base=x["sid"].rsplit("__",1)[0]; n=int(x["sid"].rsplit("__",1)[1])
            if sub in base and n in idset: want.append(x)
    if not want: print("无匹配镜头"); return
    tmp=os.path.join(rdir,"_zoom_tmp"); os.makedirs(tmp,exist_ok=True)
    dims={}
    rows=[]; labels=[]
    for x in want:
        src=x["source"]; a,b=x["in"],x["out"]; dur=b-a
        if src not in dims:
            r=run(["ffprobe","-v","error","-select_streams","v:0",
                   "-show_entries","stream=width,height","-of","csv=p=0",src])
            try: w,h=map(int,r.stdout.strip().split(",")[:2])
            except: w,h=690,1230
            dims[src]=(w,h)
        w,h=dims[src]; y1,y2=src_band(w,h)
        frames=[]
        for k,frac in enumerate((0.08,0.5,0.92)):
            fp=os.path.join(tmp,f"{x['sid']}_{k}.jpg")
            run(["ffmpeg","-y","-ss",f"{a+dur*frac:.2f}","-i",src,"-frames:v","1",fp,"-loglevel","error"])
            if os.path.exists(fp): frames.append(fp)
        if len(frames)<3: continue
        FULL_W2=170; CROP_W2=400
        im=Image.open(frames[1]).convert("RGB")
        full=im.resize((FULL_W2,int(FULL_W2*im.height/im.width)))
        dr=ImageDraw.Draw(full)
        for yy in (y1/h*full.height, y2/h*full.height):
            dr.line([(0,yy),(full.width,yy)],fill=(255,40,40),width=2)
        crops=[]
        for fp in frames:
            imf=Image.open(fp).convert("RGB")
            band=imf.crop((0,int(y1),imf.width,int(y2)))
            crops.append(band.resize((CROP_W2,int(CROP_W2*band.height/band.width))))
        row_h=max(full.height,crops[0].height)+26
        row=Image.new("RGB",(FULL_W2+3*(CROP_W2+6)+18,row_h),(15,15,15))
        row.paste(full,(0,0)); xx=FULL_W2+12
        for cr in crops: row.paste(cr,(xx,0)); xx+=CROP_W2+6
        dr=ImageDraw.Draw(row)
        dr.rectangle([0,row_h-24,row.width,row_h],fill=(0,0,0))
        dr.text((4,row_h-22),f"{x['sid']}  {x['in']}-{x['out']}s ({x['dur']}s)",
                fill=(255,230,0),font=_font(20))
        rows.append(row); labels.append(x["sid"])
    pages=[]
    for pi in range(0,len(rows),7):
        chunk=rows[pi:pi+7]
        pw=max(r.width for r in chunk); ph=sum(r.height+4 for r in chunk)
        page=Image.new("RGB",(pw,ph),(0,0,0)); yy=0
        for r in chunk: page.paste(r,(0,yy)); yy+=r.height+4
        out=os.path.join(rdir,f"_zoom_{pi//7+1:02d}.png"); page.save(out); pages.append(out)
    print(f"zoom {len(rows)}镜头 {len(pages)}页:")
    for p in pages: print(" ",p)

def cmd_triage(theme, per_file=4, rows_per_img=14):
    """主题三审：每文件横拼per_file帧+标文件名，分页成总览图。快速筛掉纯口播/电视台文件。"""
    rdir=os.path.join(REVIEW,theme)
    pj=os.path.join(rdir,"pending.json")
    if not os.path.exists(pj): print("先scan:",theme); return
    pend=json.load(open(pj))
    # 按源文件聚合
    from collections import OrderedDict
    files=OrderedDict()
    for x in pend:
        files.setdefault(x["source"],None)
    tmp=os.path.join(rdir,"_triage_tmp"); os.makedirs(tmp,exist_ok=True)
    rows=[]; labels=[]
    for fi,src in enumerate(files,1):
        dur=probe_dur(src)
        ims=[]
        for k in range(per_file):
            frac=(k+0.5)/per_file
            tp=os.path.join(tmp,f"{fi:02d}_{k}.jpg")
            run(["ffmpeg","-y","-ss",str(round(dur*frac,2)),"-i",src,"-frames:v","1",
                 "-vf",f"scale={THUMB_W}:-1",tp,"-loglevel","error"])
            if os.path.exists(tp): ims.append(Image.open(tp).convert("RGB"))
        if not ims: continue
        w=ims[0].width; h=ims[0].height
        row=Image.new("RGB",(w*per_file+ (per_file-1)*4,h),(0,0,0))
        for j,im in enumerate(ims): row.paste(im.resize((w,h)),(j*(w+4),0))
        rp=os.path.join(tmp,f"row_{fi:02d}.png"); row.save(rp)
        rows.append(rp); labels.append(f"{fi:02d} {os.path.basename(src)[:46]}")
    # 分页
    pages=[]
    for i in range(0,len(rows),rows_per_img):
        out=os.path.join(rdir,f"_triage_{i//rows_per_img+1}.png")
        make_sheet(rows[i:i+rows_per_img], labels[i:i+rows_per_img], out, cols=1)
        pages.append(out)
    print(f"三审总览({len(rows)}文件,每行{per_file}帧)：")
    for p in pages: print(" ",p)

def cmd_qc(prefix, out):
    """对库中文件名含 prefix 的片段做 头/中/尾 三帧拼图，专抓内部切点/广告尾巴。"""
    import glob as _glob
    fs=sorted(_glob.glob(os.path.join(CLIPS,"**",f"*{prefix}*.mp4"),recursive=True))
    fs=[f for f in fs if not os.path.basename(f).startswith("._")]
    if not fs: print("无匹配片段:",prefix); return
    tmp=os.path.join(REVIEW,"_qc_tmp"); os.makedirs(tmp,exist_ok=True)
    rows=[]; labels=[]
    for i,f in enumerate(fs,1):
        dur=probe_dur(f)
        strip=[]
        for frac in (0.1,0.5,0.9):
            t=round(dur*frac,2)
            tp=os.path.join(tmp,f"{i:02d}_{int(frac*100)}.jpg")
            run(["ffmpeg","-y","-ss",str(t),"-i",f,"-frames:v","1",
                 "-vf",f"scale={THUMB_W}:-1",tp,"-loglevel","error"])
            strip.append(tp)
        # 横拼头中尾
        ims=[Image.open(p).convert("RGB") for p in strip if os.path.exists(p)]
        if not ims: continue
        w=ims[0].width; h=ims[0].height
        row=Image.new("RGB",(w*3+8,h),(0,0,0))
        for j,im in enumerate(ims): row.paste(im.resize((w,h)),(j*(w+4),0))
        rp=os.path.join(tmp,f"row_{i:02d}.png"); row.save(rp)
        rows.append(rp); labels.append(f"{i:02d} {os.path.basename(f)[:40]}")
    make_sheet(rows, labels, out, cols=1)
    print(f"QC拼图(每行=1片段 头|中|尾): {out}  共{len(rows)}个片段")

if __name__=="__main__":
    if len(sys.argv)<3 or sys.argv[1] not in ("scan","build","qc","triage","zoom"):
        print(__doc__); sys.exit(1)
    if sys.argv[1]=="scan":
        date=datetime.date.today().strftime("%Y%m%d")
        if "--date" in sys.argv: date=sys.argv[sys.argv.index("--date")+1]
        cmd_scan(sys.argv[2],date)
    elif sys.argv[1]=="qc":
        out=sys.argv[3] if len(sys.argv)>3 else os.path.join(REVIEW,f"qc_{sys.argv[2]}.png")
        cmd_qc(sys.argv[2], out)
    elif sys.argv[1]=="triage":
        cmd_triage(sys.argv[2])
    elif sys.argv[1]=="zoom":
        cmd_zoom(sys.argv[2], sys.argv[3])
    else:
        cmd_build(sys.argv[2])
