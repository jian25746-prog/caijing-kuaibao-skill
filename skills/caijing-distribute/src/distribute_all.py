#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribute_all.py — 一键把某节目某天所有 clip 分发到三平台（YouTube / 小红书 / 视频号）。
前置：当天 发布物料.json 已生成（由 SKILL 的模型步骤写好）+ 三平台登录态有效（cookie/token）。
流程：抽封面 → 逐平台 × 逐 clip 调各自上传脚本 → 汇总。无人值守。
🔴 今日头条与 视频号/YT 并行跑（2026-09-09 起）：头条耗时最长（防限流间隔拖到近半小时），
   过去排最后串行、白白拉长总时长；三者各走独立 Chrome profile 或纯 API，互不冲突，改用线程并行。

用法:
  python3 distribute_all.py 7点财经快报 6月20日
  python3 distribute_all.py 7点财经快报 6月20日 --platforms xhs,sph   # 只小红书+视频号
  python3 distribute_all.py 7点财经快报 6月20日 --clips 3,9           # 只指定 clip
  python3 distribute_all.py 7点财经快报 6月20日 --no-publish          # 演练（小红书/视频号只填不发，YT dry-run）
  python3 distribute_all.py 7点财经快报 6月20日                      # 🔴默认就会定时铺开 06:30-08:30
  python3 distribute_all.py 7点财经快报 6月20日 --spread 07:00-12:00  # 自定义时段
  python3 distribute_all.py 7点财经快报 6月20日 --no-spread           # 关掉定时、立即连发（不推荐）

🔴 定时铺开＝**默认行为**（2026-08-04 起，窗口默认 06:30-08:30 早通勤时段）：
  背景：实测 258 条数据发现，1/3 的日子整批只有 44~124 播放（正常日 1,200~3,400），均匀塌陷=批次级压制；
  而平台明令「同质化内容/批量生产」属限流项。原节奏是每 ~4 分钟连发 10 条、35 分钟灌完，极像机器灌号。
  --spread 起-止 会给每条算一个当天的发表时刻（均匀分布 + 随机抖动），走视频号**自带的「定时发表」**，
  一轮上传（~30分钟）跑完就关浏览器，**之后由平台按点自动发，本机可关机/休眠**。
  YouTube 走 API 不参与定时，仍连续快跑。
  ⚠️ 所有时刻必须晚于当前时间；早于「现在+10分钟」的槽位会自动顺延（并打印提示）。
"""
import os, sys, json, subprocess, time, random, threading
from datetime import datetime, timedelta

ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
SRC = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# 平台: (中文名, 脚本, 成功关键词)
# ⚠️ 小红书=半自动保号（2026-07-01 因脚本自动发布被封7天+申诉失败）→ 只生成手动包、绝不自动发。
PLAT = {
    "yt":  ("YouTube", "youtube_upload.py",     ["上传完成", "videoId="]),
    "xhs": ("小红书",  "xhs_manual_pack.py",    ["手动包已生成"]),
    "tt":  ("今日头条", "toutiao_upload.py",     ["PUBLISH_OK"]),
    "sph": ("视频号",  "shipinhao_upload.py",   ["PUBLISH_OK"]),
}
ORDER = ["yt", "tt", "sph"]  # 小红书手动包已提到最前面单独生成（见 main），这里只排自动平台；YT 最快先跑、视频号最慢压后
# 🔴 今日头条：竖屏(9:16)在头条**不支持定时发表**（2026-08-19 实测页面红字：无原创/赞赏/定时），
#   没法像视频号那样用 --spread 把发表时刻摊开 → 只能连发。为避免重蹈「每 4 分钟连灌 10 条」被判批量生产的覆辙，
#   头条改为**每条之间插入 2~5 分钟随机间隔**（下方 TT_GAP），拉长节奏、降低机器灌号特征。
TT_GAP = (120, 300)  # 秒


def arg_val(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def load(show, date):
    cfg = json.load(open(os.path.join(ROOT, "config", "shows.json"), encoding="utf-8"))
    shows = {k: v for k, v in cfg.items() if not k.startswith("_")}
    canon = show if show in shows else next((k for k, v in shows.items() if show in v.get("aliases", [])), show)
    data = json.load(open(os.path.join(ROOT, "物料", canon, date, "发布物料.json"), encoding="utf-8"))
    return canon, data


def run(script, args):
    r = subprocess.run([PY, "-u", os.path.join(SRC, script)] + args, capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def main():
    show, date = sys.argv[1], sys.argv[2]
    # 🔴 默认必须含全部 4 个平台（2026-08-19 改）：原默认漏了 tt(今日头条)，导致每次不显式写 --platforms
    # 就静默跳过头条、用户被迫每天口头补一句。同「打散发布」教训：正确行为不该依赖调用方记得加参数。
    req_plats = arg_val("--platforms", "yt,tt,xhs,sph").split(",")
    plats = [p for p in ORDER if p in req_plats]
    clip_filter = [int(x) for x in arg_val("--clips").split(",")] if "--clips" in sys.argv else None
    publish = "--no-publish" not in sys.argv
    # 🔴 打散发布是**默认行为**（2026-08-04 改）：默认 06:30-08:30 早通勤时段均匀铺开。
    # 血泪：8/1 把 --spread 做成可选参数，8/4 分发会话不知道要加 → 10条又挤在34分钟内连发、定时完全没生效。
    # 正确的行为不该依赖调用方记得加参数，故改为默认开启；要立即连发用 --no-spread 显式关闭。
    spread = None if "--no-spread" in sys.argv else arg_val("--spread", "06:30-08:30")

    canon, data = load(show, date)
    clips = [c["clip_num"] for c in data["clips"] if (clip_filter is None or c["clip_num"] in clip_filter)]
    print(f"=== 分发 {canon} {date} | clips={clips} | 平台={req_plats} | 发布={publish} ===\n", flush=True)

    # 视频号定时发表时刻表：时段内均匀分布 + ±40% 槽距抖动；全部推到「现在+10分钟」之后
    sched = {}
    if spread and "sph" in plats:
        try:
            s_str, e_str = spread.split("-")
            now = datetime.now()
            base = now.replace(second=0, microsecond=0)
            start = base.replace(hour=int(s_str[:2]), minute=int(s_str[3:5]))
            end = base.replace(hour=int(e_str[:2]), minute=int(e_str[3:5]))
            floor = now + timedelta(minutes=10)
            if start < floor:
                print(f"⚠️ 起始 {s_str} 早于「现在+10分钟」，顺延到 {floor.strftime('%H:%M')}", flush=True)
                start = floor
            if end <= start:
                end = start + timedelta(hours=max(1, len(clips) // 3))
            step = (end - start) / max(1, len(clips) - 1) if len(clips) > 1 else timedelta(0)
            prev = None
            for i, cn in enumerate(clips):
                # ±25% 抖动：保持"均匀铺开"的同时避免精确等距——等距本身就是机器灌号特征
                t = start + step * i + timedelta(minutes=random.uniform(-0.25, 0.25) * step.total_seconds() / 60)
                t = max(t, floor)
                if prev:                      # 保证严格递增、至少隔 6 分钟
                    t = max(t, prev + timedelta(minutes=6))
                prev = t
                sched[cn] = t.strftime("%H:%M")
            print("⏱️  视频号定时发表时刻表：" + " ".join(f"第{c}条·{sched[c]}" for c in clips), flush=True)
            print("    上传约 30 分钟跑完 → 之后平台按点自动发，**本机可关机/休眠**。\n", flush=True)
        except Exception as e:
            print(f"⚠️ --spread 解析失败（按立即发表执行）：{e}\n", flush=True)
            sched = {}

    # 抽封面（一次全做）；节目未配 clip_glob（如名词视频：单条+自带 hook.png 封面）则跳过，不刷 KeyError
    _cfg_all = json.load(open(os.path.join(ROOT, "config", "shows.json"), encoding="utf-8"))
    _cfg_show = next((v for k, v in _cfg_all.items()
                      if not k.startswith("_") and (k == canon or show in v.get("aliases", []))), {})
    if _cfg_show.get("clip_glob"):
        cov = run("extract_cover.py", [show, date]).strip().splitlines()
        print("[封面] " + (cov[-1][:80] if cov else "无输出"), flush=True)
    else:
        print("[封面] 跳过（该节目未配 clip_glob，封面由发布物料直接指定）", flush=True)

    results, album_fails, t_all = [], [], time.time()

    # 小红书手动包最先出（用户反馈 2026-07-28）：让用户能在 YT/视频号自动上传的同时手动发小红书，两边并行不用干等。
    # 半自动保号：整天一次性生成手动包，绝不自动发布（2026-07-01 小红书封号教训）
    # 🔴 无条件生成（2026-08-04 改）：原来卡了 `if "xhs" in req_plats`，而日常都用 --platforms yt,sph 调用
    #   → xhs 被排除、手动包整整几天没生成，每次都要事后手动补跑。同 --spread 的教训：
    #   纯文件操作、零风险、必然有用的步骤，不该依赖调用方记得把 xhs 写进 --platforms。
    out = run("xhs_manual_pack.py", [show, date])
    ok = any(k in out for k in PLAT["xhs"][2])
    results.append(("小红书·手动包", ok, 0, None))
    print(("✓ 小红书 已生成【手动发布包】→ AirDrop 手机、App 手动发（非自动，保号；可与下面自动平台同时进行）"
           if ok else "✗ 小红书手动包生成失败"), flush=True)
    if not ok:
        print("  ↳ " + " | ".join(out.strip().splitlines()[-3:])[:220], flush=True)

    def run_platform(p, tag_prefix=""):
        """跑单个平台的全部 clip，返回 (results, album_fails)。放进独立线程时用 tag_prefix 区分打印行，避免和其它平台交错时看混。"""
        name, script, oks = PLAT[p]
        local_results, local_fails = [], []
        for ci, cn in enumerate(clips):
            tag = f"{name}·第{cn}条" + (f"→{sched[cn]}" if p == "sph" and cn in sched else "")
            if p == "yt":
                args = [show, date, "--only", str(cn)] + (["--public"] if publish else ["--dry-run"])
            else:
                args = [show, date, "--clip", str(cn)] + (["--publish"] if publish else [])
                if p == "sph" and cn in sched:
                    args += ["--at", sched[cn]]
            t0 = time.time()
            out = run(script, args)
            dt = int(time.time() - t0)
            ok = any(k in out for k in oks)
            album, album_why = None, ""
            if p == "sph":
                line = next((l for l in out.splitlines() if l.strip().startswith("合集:")), None)
                # 2026-09-16：只留 ✓/✗ 把原因全丢了，合集连挂 6 周没人看见。
                # 现在失败必带原句；连「合集:」这行都没有（脚本早退）也算失败，不再静默。
                album_why = line.split("合集:", 1)[1].strip() if line else "（没有「合集:」行，脚本可能提前退出）"
                album = ("OK" in line) if line else False
                if not album:
                    local_fails.append(f"{tag} ← {album_why}")
            local_results.append((tag, ok, dt, album))
            mark = "✓" if ok else "✗"
            extra = "" if album is None else ("  合集✓" if album else "  合集✗")
            print(f"{tag_prefix}{mark} {tag} ({dt}s){extra}", flush=True)
            if album is False:
                print(f"{tag_prefix}  ↳ 合集未生效: {album_why}", flush=True)
            if not ok:
                print(f"{tag_prefix}  ↳ " + " | ".join(out.strip().splitlines()[-3:])[:220], flush=True)
            if p == "tt" and publish and ci < len(clips) - 1:
                gap = random.randint(*TT_GAP)
                print(f"{tag_prefix}  · 头条防限流间隔 {gap}s…", flush=True)
                time.sleep(gap)
        return local_results, local_fails

    # 🔴 今日头条与 视频号/YT 并行跑（2026-09-09 改，用户要求提效）：三者各走独立浏览器 profile
    # （tt_profile / sph_profile）或纯 API（YT），互不占用同一个 Chrome 实例，没有资源冲突，可以真并行。
    # 头条本身耗时最长（7条×(~45s发布+2~5分钟防限流间隔)≈半小时），过去排在视频号后面串行、白白拖长总时长。
    # 用线程而非多进程：run() 内部是 subprocess.run 阻塞调用，等待子进程时会释放 GIL，线程间不会互相卡顿。
    threads, tt_holder = [], {}
    if "tt" in plats:
        def _run_tt():
            tt_holder["res"], tt_holder["fails"] = run_platform("tt", tag_prefix="  [头条] ")
        th = threading.Thread(target=_run_tt)
        th.start()
        threads.append(th)
        plats = [p for p in plats if p != "tt"]

    for p in plats:
        res, fails = run_platform(p, tag_prefix="  ")
        results.extend(res)
        album_fails.extend(fails)

    for th in threads:
        th.join()
    if "res" in tt_holder:
        results.extend(tt_holder["res"])
        album_fails.extend(tt_holder["fails"])

    print("\n=== 汇总 ===", flush=True)
    for tag, ok, dt, album in results:
        extra = "" if album is None else ("  合集✓" if album else "  合集✗")
        print(f"  {'✓' if ok else '✗'} {tag}  {dt}s{extra}", flush=True)
    okn = sum(1 for _, ok, _, _ in results if ok)
    print(f"\n成功 {okn}/{len(results)} · 总耗时 {int(time.time()-t_all)}s", flush=True)
    if album_fails:
        print("⚠️ 合集漏选（需手动补）:", flush=True)
        for f in album_fails:
            print(f"     · {f}", flush=True)
        print("     ↳ 合集满 500 条会静默拒绝；先去 视频号助手→视频管理→合集 看目标合集条数。", flush=True)

    # 受众数据回流（选题闭环）：发布完顺带抓视频号近7天播放数据 + 刷新观众偏好.json。
    # 失败只影响偏好文件不更新（明日4am自动沿用旧版/跳过），绝不影响发布结果。
    try:
        print("\n=== 受众数据回流 ===", flush=True)
        out = run("sph_fetch_stats.py", ["--days", "7", "--aggregate"])
        for l in out.strip().splitlines()[-3:]:
            print("  " + l[:160], flush=True)
    except Exception as e:
        print(f"  ⚠️ 回流失败（不影响发布）: {e}", flush=True)


if __name__ == "__main__":
    main()
