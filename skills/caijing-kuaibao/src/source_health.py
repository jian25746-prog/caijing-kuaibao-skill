#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
source_health.py — 信源健康度监控（2026-09-25 新增）

为什么要有它：
  SCMP 两次"静默失效"（6/12 频道号配错；9月 港媒白天作息与20:00–04:00窗口错位，每晚仅~1.9条），
  华尔街见闻 9/18–9/23 接口域名下线断供6天——都没有任何报警。原因是 STEP1 规则
  "单站点失败立即跳过"，且源返回0条时 HTTP 正常、连"失败"都不算，日志每次给个说得通的
  理由就翻篇。"下次多注意"防不住第三次，所以改由确定性脚本逐日记账、自动标红。

用法（STEP1 第七阶段，主进程）:
  python3 source_health.py <信源健康度.json> <YYYY-MM-DD> '<今日各源条数JSON>'
  今日条数 = 各源【时间窗内拿到的有效条目数】（有标题+时间；URL缺失不计）；
  抓取报错/无法访问的源写字符串 "failed"。例:
  '{"CNBC":9,"SCMP-92":0,"SCMP-12":1,"华尔街见闻":"failed","财联社":5}'

输出：一段可直接贴进制作日志【零、信源健康度】的文字；台账原子写回，保留近30天。
判定（按严重度）:
  🔴 今日抓取失败（failed）
  🔴 连续≥3天 0条或失败
  🟡 今日未上报（历史有记录、今天却没出现——源被悄悄漏抓）
  🟡 7日均值 < 1（至少有3天数据才判）
"""
import json, os, sys, tempfile
from datetime import date, timedelta

KEEP_DAYS = 30
ZERO_STREAK = 3
AVG_WINDOW = 7
AVG_FLOOR = 1.0
AVG_MIN_SAMPLES = 3


def load(path):
    if not os.path.exists(path):
        return {"sources": {}}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("sources", {})
        return d
    except (json.JSONDecodeError, OSError):
        return {"sources": {}}   # 台账损坏不阻断4am，重新开始计


def save_atomic(path, data):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def as_num(v):
    return 0 if v == "failed" else int(v)


def main():
    if len(sys.argv) != 4:
        print(__doc__); sys.exit(2)
    path, today_s, counts_s = sys.argv[1], sys.argv[2], sys.argv[3]
    today = date.fromisoformat(today_s)
    counts = json.loads(counts_s)

    data = load(path)
    src = data["sources"]
    for name, v in counts.items():
        if v != "failed":
            v = int(v)
        src.setdefault(name, {})[today_s] = v      # 同日重跑直接覆盖

    # 裁剪到近30天
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    for name in list(src):
        src[name] = {k: v for k, v in src[name].items() if k >= cutoff}
        if not src[name]:
            del src[name]

    red, yellow, ok = [], [], []
    for name in sorted(src):
        hist = src[name]
        days = sorted(hist)
        if today_s not in hist:
            if days:
                yellow.append(f"🟡 {name}：今日未上报（最近一次记录 {days[-1]}）——疑似被漏抓")
            continue
        v = hist[today_s]
        # 连续 0/失败 天数（从今天往回数，只数连续有记录的日子）
        streak, d = 0, today
        while d.isoformat() in hist and as_num(hist[d.isoformat()]) == 0:
            streak += 1
            d -= timedelta(days=1)
        recent = [as_num(hist[k]) for k in days if k > (today - timedelta(days=AVG_WINDOW)).isoformat()]
        avg = sum(recent) / len(recent) if recent else 0.0

        if v == "failed":
            red.append(f"🔴 {name}：今日抓取失败" + (f"（已连续{streak}天）" if streak > 1 else ""))
        elif streak >= ZERO_STREAK:
            red.append(f"🔴 {name}：连续{streak}天 0 条")
        elif len(recent) >= AVG_MIN_SAMPLES and avg < AVG_FLOOR:
            yellow.append(f"🟡 {name}：近{len(recent)}天均值 {avg:.1f} 条/晚（今日{v}）")
        else:
            ok.append(f"{name} {v}")

    save_atomic(path, data)

    print("【零、信源健康度】")
    if not red and not yellow:
        print("✅ 全部正常：" + " / ".join(ok))
    else:
        for line in red + yellow:
            print(line)
        if ok:
            print("正常：" + " / ".join(ok))
    print(f"（判定：失败或连续{ZERO_STREAK}天0条=🔴；7日均值<{AVG_FLOOR:g}或今日未上报=🟡）")


if __name__ == "__main__":
    main()
