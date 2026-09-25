#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_cover.py — 给某节目某天的每条 clip 从成品视频抽一帧做封面/缩略图。

用法:  python3 extract_cover.py <节目名> <日期目录> [--ts 秒] [--seq 序号=秒,序号=秒]
例:    python3 extract_cover.py 7点财经播报 6月13日
       python3 extract_cover.py 7点财经播报 6月13日 --ts 2.5 --seq 5=4,9=1.2

默认抽 t=2.0s（开头 hook 帧，通常已带头条大字）。封面写到
节目上传/物料/<节目>/<日期>/封面_第X条.png。只读视频、不改节目目录。
"""
import sys, os, re, json, glob, subprocess
from shutil import which

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
CONFIG = os.path.join(UPLOAD_ROOT, "config", "shows.json")
CN = {1: '一', 2: '二', 3: '三', 4: '四', 5: '五',
      6: '六', 7: '七', 8: '八', 9: '九', 10: '十'}
CN_NUM = {v: k for k, v in CN.items()}


def cn_to_int(s):
    if s == '十':
        return 10
    if s.endswith('十'):
        return CN_NUM.get(s[:-1], 0) * 10
    if s.startswith('十'):
        return 10 + CN_NUM.get(s[1:], 0)
    return CN_NUM.get(s, 0)


def ffmpeg_bin():
    if which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def parse_args(argv):
    show, date = argv[0], argv[1]
    ts, per = 2.0, {}
    i = 2
    while i < len(argv):
        if argv[i] == "--ts":
            ts = float(argv[i + 1]); i += 2
        elif argv[i] == "--seq":
            for kv in argv[i + 1].split(','):
                k, v = kv.split('='); per[int(k)] = float(v)
            i += 2
        else:
            i += 1
    return show, date, ts, per


def main():
    if len(sys.argv) < 3:
        print("用法: python3 extract_cover.py <节目名> <日期目录> [--ts 秒] [--seq 序号=秒,...]")
        sys.exit(1)
    show, date, ts, per = parse_args(sys.argv[1:])
    _all = json.load(open(CONFIG, encoding='utf-8'))
    _shows = {k: v for k, v in _all.items() if not k.startswith('_')}
    cfg = _shows.get(show)
    if not cfg:  # 试别名（如 "7点财经快报" → 文件夹名 "7点财经播报"）
        for _k, _c in _shows.items():
            if show in _c.get('aliases', []):
                show, cfg = _k, _c
                break
    if not cfg:
        print(f"✗ shows.json 未配置节目: {show}")
        sys.exit(1)
    ddir = os.path.join(cfg["base"], date)
    # 支持两种命名：①第X条.mp4（中文数字） ②成片_XX_主题.mp4（阿拉伯数字，2026-07-03起）；编号=首条评论序号
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
                clips.append((cn_to_int(m.group(1)), fp)); continue
            m = clip_pat_num.search(base)
            if m:
                clips.append((int(m.group(1)), fp))
    clips.sort()
    if not clips:
        print(f"✗ 未找到分条视频: {ddir}/{globs}")
        sys.exit(1)

    outdir = os.path.join(UPLOAD_ROOT, "物料", show, date)
    os.makedirs(outdir, exist_ok=True)
    fb = ffmpeg_bin()
    done = 0
    for seq, fp in clips:
        t = per.get(seq, ts)
        outp = os.path.join(outdir, f"封面_第{CN.get(seq, seq)}条.png")
        cmd = [fb, "-y", "-ss", str(t), "-i", fp, "-frames:v", "1", "-q:v", "2", outp]
        r = subprocess.run(cmd, capture_output=True, text=True)
        ok = os.path.exists(outp) and os.path.getsize(outp) > 0
        done += ok
        msg = f"{'✓' if ok else '✗'} 第{CN.get(seq, seq)}条 @ {t}s → {os.path.basename(outp)}"
        if not ok:
            msg += f"  [{r.stderr.strip()[-140:]}]"
        print(msg)
    print(f"\n封面完成 {done}/{len(clips)} → {outdir}")


if __name__ == "__main__":
    main()
