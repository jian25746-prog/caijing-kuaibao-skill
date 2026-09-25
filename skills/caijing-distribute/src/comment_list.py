#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comment_list.py — 生成当天「首条评论」一键复制清单（视频号首评手动发用）。
背景：视频号网页后台无法给自己视频发新评论（评论是App观众动作、后台仅能管理已有评论）；
     YouTube 首评脚本已自动发。故视频号首评走手动，本工具把当天10条首评汇总成好复制的清单。
用法: python3 comment_list.py <节目> <日期>
产出: 物料/<节目>/<日期>/视频号首评清单.txt
"""
import os, sys, json

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内


def resolve(show):
    cfg = json.load(open(os.path.join(UPLOAD_ROOT, "config", "shows.json"), encoding="utf-8"))
    shows = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return show if show in shows else next((k for k, v in shows.items() if show in v.get("aliases", [])), show)


def main():
    if len(sys.argv) < 3:
        print("用法: python3 comment_list.py <节目> <日期>"); sys.exit(1)
    show, date = resolve(sys.argv[1]), sys.argv[2]
    mp = os.path.join(UPLOAD_ROOT, "物料", show, date, "发布物料.json")
    if not os.path.exists(mp):
        print(f"✗ 缺物料: {mp}"); sys.exit(1)
    clips = json.load(open(mp, encoding="utf-8"))["clips"]

    lines = [f"视频号首评清单 · {show} · {date}",
             "（视频号首评需手机App手动发+长按置顶；下面按发布顺序，逐条复制粘贴即可）",
             "=" * 46, ""]
    for c in clips:
        cn = c["clip_num"]
        pinned = c.get("pinned_comment", "")
        title = c.get("真实内容", "")[:26]
        lines += [f"▼ 第{cn}条 · {title}", pinned, ""]
    out = os.path.join(UPLOAD_ROOT, "物料", show, date, "视频号首评清单.txt")
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    print(f"✓ 视频号首评清单已生成 · {len(clips)}条")
    print(f"  → {out}")


if __name__ == "__main__":
    main()
