#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compose.py — 7点财经 成片合成器（PNG切片卡片 + 素材库B-roll → 12秒成片）

生产规格（用户定稿）：
  • 单条时长固定 12 秒
  • hook 卡全程 12s 作底层
  • narration 各页 + bottom 卡 均分 12s（n页+bottom => 每段 12/(n+1) 秒）
  • B-roll 背景：顶/底两头模糊（盖台标/字幕/UI瑕疵 + 让文字突出），中央保持清晰
  • 末尾配等长动感音乐

用法：
  python3 compose.py <png_dir> <slice_idx> --topic 美国 [--music x.mp3] [--out out.mp4]
  • png_dir : 当天 PNG素材 目录（含 hook_xx / narration_xx_pN / bottom_xx）
  • topic   : 在素材库中按该关键词筛选B-roll（匹配 topic/category/visual）
  • music   : 可选；缺省自动从 素材库/music/ 随机取一首；都没有则无声

依赖：系统 ffmpeg / ffprobe。
"""
import os, sys, re, glob, json, subprocess, random, tempfile

ROOT   = os.path.abspath(os.environ.get("BROLL_LIBRARY") or os.path.join(os.path.dirname(__file__), ".."))
CLIPS  = os.path.join(ROOT, "clips")
LIB    = os.path.join(ROOT, "library.json")
MUSIC  = os.path.join(ROOT, "music")
MASK   = os.path.join(os.path.dirname(__file__), "mask_1440x2560.png")

W, H, FPS = 1440, 2560, 30
TOTAL = 12.0
# 模糊带高度：单一参数，上下一致(结构保证对称)；调大到能盖住源片原有字幕/台标
# 上带=[0,BAND_H]，下带=[H-BAND_H,H]，中央清晰=[BAND_H,H-BAND_H]
BAND_H = 700
SHARP_TOP, SHARP_BOT = BAND_H, H - BAND_H
# 100%模糊：缩到 1/DOWNSCALE(≈5px宽)再放大 + 重gblur => 纯色，完全无细节
DOWNSCALE  = 300
BLUR_SIGMA = 80
SEG_DUR    = 2.0     # 背景每段2秒
NSEG       = 6       # 背景至少6个不同片段(6×2=12s)

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def ensure_mask():
    if os.path.exists(MASK): return
    # 二值硬边蒙版：中央带=255(清晰)，两头=0(模糊)，边界100%锐利无渐变
    expr = f"if(between(Y,{SHARP_TOP},{SHARP_BOT}),255,0)"
    run(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black:s={W}x{H}:d=1",
         "-vf",f"format=gray,geq=lum='{expr}'","-frames:v","1",MASK,"-loglevel","error"])

def find_cards(png_dir, idx):
    g = lambda pat: sorted(glob.glob(os.path.join(png_dir, pat)))
    hook = g(f"hook_{idx:02d}_*.png")
    narr = sorted(g(f"narration_{idx:02d}_p*.png"),
                  key=lambda f:int(re.search(r"_p(\d+)_",f).group(1)))
    bottom = g(f"bottom_{idx:02d}_*.png")
    if not hook or not narr:
        raise SystemExit(f"切片{idx:02d}: 缺 hook 或 narration PNG")
    return hook[0], narr, (bottom[0] if bottom else None)

def pick_bg_segments(theme):
    """选 NSEG 个【不同】片段：先同theme，不足从【通用干净池】补，绝不回退随机全库脏池。"""
    lib = json.load(open(LIB))["clips"] if os.path.exists(LIB) else []
    if not lib: raise SystemExit("素材库为空，无法取B-roll")
    def take_distinct(cands, have):
        out=[]
        for c in cands:
            if c["file"] in have: continue
            have.add(c["file"]); out.append(c)
        return out
    exact   = [c for c in lib if c.get("theme")==theme]
    generic = [c for c in lib if c.get("generic")]
    random.shuffle(exact); random.shuffle(generic)
    have=set(); seq=take_distinct(exact, have)            # 同题材优先
    if len(seq)<NSEG: seq += take_distinct(generic, have) # 通用干净池补足
    seq = seq[:NSEG]
    # 不足6段时：绝不重复同一画面——用长片段的不同2秒窗口补(同条内视觉不同段)
    if len(seq)<NSEG:
        longs=sorted([c for c in seq if float(c.get("dur",0))>=2*SEG_DUR+0.1],
                     key=lambda c:-float(c["dur"]))
        i=0
        while len(seq)<NSEG and longs:
            c=dict(longs[i%len(longs)])
            c["ws"]=round((i//len(longs)+1)*SEG_DUR,2)    # 取后续不重叠窗口
            if c["ws"]+SEG_DUR<=float(c["dur"])+0.05: seq.append(c)
            else: break
            i+=1
    return seq

def pick_music(arg):
    if arg: return arg
    if os.path.isdir(MUSIC):
        ms = [os.path.join(MUSIC,f) for f in os.listdir(MUSIC)
              if f.lower().endswith((".mp3",".m4a",".wav",".aac"))]
        if ms: return random.choice(ms)
    return None

def compose(png_dir, idx, topic, music, out, plan_clips=None):
    hook, narr, bottom = find_cards(png_dir, idx)
    n_seg = len(narr) + (1 if bottom else 0)
    seg = TOTAL / n_seg
    print(f"切片{idx:02d}: {len(narr)}页narration + {'1' if bottom else '0'}bottom = {n_seg}段 × {seg:.2f}s")

    # plan_clips: 由模型语义匹配指定的片段dict列表(含file/dur)；不传则退回题材自动匹配
    bg_clips = plan_clips if plan_clips else pick_bg_segments(topic)
    print(f"   背景{len(bg_clips)}段: " + " | ".join(os.path.basename(c['file'])[:18] for c in bg_clips))
    with tempfile.TemporaryDirectory() as tmp:
        # 1) 背景 = NSEG个不同片段 × SEG_DUR秒 硬切拼接
        segfiles=[]
        for j,c in enumerate(bg_clips):
            src=os.path.join(ROOT,c["file"]); d=float(c.get("dur",SEG_DUR))
            segp=os.path.join(tmp,f"seg{j}.mp4")
            if d>=SEG_DUR+0.05:
                # 窗口起点：分配器可用 "ws" 指定（全天去重的关键）；未指定才随机
                st=c.get("ws")
                st=round(random.uniform(0,d-SEG_DUR),2) if st is None else min(float(st),max(0.0,d-SEG_DUR))
                run(["ffmpeg","-y","-ss",str(st),"-t",str(SEG_DUR),"-i",src,"-an",
                     "-r",str(FPS),"-c:v","libx264","-crf","18","-pix_fmt","yuv420p",segp,"-loglevel","error"])
            else:                                        # 短片段循环补到2秒
                run(["ffmpeg","-y","-stream_loop","-1","-t",str(SEG_DUR),"-i",src,"-an",
                     "-r",str(FPS),"-c:v","libx264","-crf","18","-pix_fmt","yuv420p",segp,"-loglevel","error"])
            segfiles.append(segp)
        lst=os.path.join(tmp,"bg.txt")
        with open(lst,"w") as f:
            for s in segfiles: f.write(f"file '{s}'\n")
        bg=os.path.join(tmp,"bg.mp4")
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",lst,"-t",str(TOTAL),"-c","copy",bg,"-loglevel","error"])

        # 2) 组装 filter：中央清晰 + 上下=【中央镜像反射→重模糊】(镜面蒙版) + 叠卡片
        inputs = ["-i",bg,"-i",hook]
        for p in narr: inputs += ["-i",p]
        if bottom: inputs += ["-i",bottom]

        CENTER_H = SHARP_BOT - SHARP_TOP
        blur = f"scale=iw/{DOWNSCALE}:ih/{DOWNSCALE},scale={W}:{BAND_H}:flags=bilinear,gblur=sigma={BLUR_SIGMA}"
        fc  = "[0:v]split=3[c0][c1][c2];"
        fc += f"[c0]crop={W}:{CENTER_H}:0:{SHARP_TOP}[ctr];"                         # 中央清晰
        fc += f"[c1]crop={W}:{BAND_H}:0:{SHARP_TOP},vflip,{blur}[top];"              # 上带=中央顶部镜像+模糊
        fc += f"[c2]crop={W}:{BAND_H}:0:{SHARP_BOT-BAND_H},vflip,{blur}[bot];"       # 下带=中央底部镜像+模糊
        fc += "[top][ctr][bot]vstack=3[bg0];"
        fc += "[bg0][1:v]overlay=0:0[s0];"          # hook 全程
        cur = "s0"; vin = 2                          # narration/bottom 从输入2开始
        cards = list(range(len(narr))) + ([ "bot" ] if bottom else [])
        for k,_ in enumerate(cards):
            a = k*seg; bb = (k+1)*seg
            nxt = f"s{k+1}"
            fc += f"[{cur}][{vin}:v]overlay=0:0:enable='between(t,{a:.3f},{bb:.3f})'[{nxt}];"
            cur = nxt; vin += 1
        fc = fc.rstrip(";")
        last = cur

        cmd = ["ffmpeg","-y"] + inputs
        if music:
            cmd += ["-stream_loop","-1","-i",music]
            mi = 2 + len(narr) + (1 if bottom else 0)   # 音乐输入序号(bg,hook,narr...,bottom)
            fc += f";[{mi}:a]afade=t=out:st={TOTAL-1.2:.2f}:d=1.2,atrim=0:{TOTAL}[aud]"
            cmd += ["-filter_complex",fc,"-map",f"[{last}]","-map","[aud]",
                    "-c:a","aac","-b:a","160k"]
        else:
            cmd += ["-filter_complex",fc,"-map",f"[{last}]"]
        cmd += ["-t",str(TOTAL),"-r",str(FPS),"-c:v","libx264","-crf","19",
                "-preset","medium","-pix_fmt","yuv420p",out,"-loglevel","error"]
        r = run(cmd)
        if r.returncode!=0:
            print("❌ ffmpeg:\n"+r.stderr[-1200:]); sys.exit(1)
    mb = os.path.getsize(out)/1024/1024
    print(f"✅ 成片: {out}  ({mb:.1f}MB, {TOTAL:.0f}s, 音乐={'有' if music else '无'})")

if __name__=="__main__":
    a=sys.argv
    if len(a)<3: print(__doc__); sys.exit(1)
    png_dir=a[1]; idx=int(a[2])
    def opt(name,d=None):
        return a[a.index(name)+1] if name in a else d
    topic=opt("--topic","")
    music=pick_music(opt("--music"))
    out=opt("--out", os.path.join(os.getcwd(), f"成片_slice{idx:02d}.mp4"))
    compose(png_dir, idx, topic, music, out)
