#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xhs_manual_pack.py — 小红书【半自动保号】打包器（纯文件操作，绝不碰浏览器/自动化）。
背景：2026-07-01 小红书因"脚本/AI自动发布"实际封号7天+申诉失败，全自动路径已弃用。
改为：机器把每条的 视频+封面+标题+正文(含话题)+首评 全部备好、复制文件到一个文件夹，
     用户 AirDrop 到手机 → 小红书 App 手动发（真人操作=零封号风险）。

用法: python3 xhs_manual_pack.py <节目> <日期>
产出: 物料/<节目>/<日期>/小红书手动包/  （含各条视频、封面、发布清单.txt）
"""
import os, sys, json, shutil

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内


def resolve(show):
    cfg = json.load(open(os.path.join(UPLOAD_ROOT, "config", "shows.json"), encoding="utf-8"))
    shows = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return show if show in shows else next((k for k, v in shows.items() if show in v.get("aliases", [])), show)


def main():
    if len(sys.argv) < 3:
        print("用法: python3 xhs_manual_pack.py <节目> <日期>"); sys.exit(1)
    show, date = resolve(sys.argv[1]), sys.argv[2]
    only = int(sys.argv[sys.argv.index("--clip") + 1]) if "--clip" in sys.argv else None
    mp = os.path.join(UPLOAD_ROOT, "物料", show, date, "发布物料.json")
    if not os.path.exists(mp):
        print(f"✗ 缺物料: {mp}"); sys.exit(1)
    clips = json.load(open(mp, encoding="utf-8"))["clips"]
    if only is not None:
        clips = [c for c in clips if c["clip_num"] == only]

    outdir = os.path.join(UPLOAD_ROOT, "物料", show, date, "小红书手动包")
    os.makedirs(outdir, exist_ok=True)

    lines = [f"小红书手动发布清单 · {show} · {date}",
             "（手机 AirDrop 本文件夹 → 小红书 App 手动发。真人操作，零封号风险）",
             "=" * 46, ""]
    n_media = 0
    for c in clips:
        cn = c["clip_num"]
        xhs = c.get("xiaohongshu", {})
        title = xhs.get("标题", "")
        body = xhs.get("正文", "")
        pinned = c.get("pinned_comment", "")
        # 复制视频 + 封面到打包文件夹（便于整包 AirDrop）
        vid, cov = c.get("video"), c.get("cover")
        vname = cname = ""
        if vid and os.path.exists(vid):
            vname = f"第{cn}条_视频.mp4"
            shutil.copy2(vid, os.path.join(outdir, vname)); n_media += 1
        if cov and os.path.exists(cov):
            cname = f"第{cn}条_封面.png"
            shutil.copy2(cov, os.path.join(outdir, cname))
        warn = "  ⚠️标题超20字，发前删短！" if len(title) > 20 else ""
        lines += [
            f"━━━ 第{cn}条 · {c.get('真实内容','')[:26]} ━━━",
            f"【标题】{len(title)}/20字{warn}",
            title,
            "",
            "【正文】（含话题，整段复制）",
            body,
            "",
            f"【视频】{vname or '(源缺失)'}    【封面】{cname or 'App内选帧'}",
            f"【发布后·置顶首评】{pinned}",
            "", ""]
    txt = os.path.join(outdir, "发布清单.txt")
    open(txt, "w", encoding="utf-8").write("\n".join(lines))
    print(f"✓ 小红书手动包已生成 · {len(clips)}条 · 复制{n_media}个视频")
    print(f"  → {outdir}")
    print(f"  清单: {txt}")
    print("  下一步：AirDrop 这个文件夹到手机 → 小红书App手动发")


if __name__ == "__main__":
    main()
