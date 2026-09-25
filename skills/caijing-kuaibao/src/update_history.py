#!/usr/bin/env python3
"""
update_history.py — 新闻历史记录更新脚本（防 JSON 损坏）

用法:
  python3 update_history.py <history_json> <today_entries_json>

- history_json       : 新闻历史记录.json 路径
- today_entries_json : 今日新增条目文件路径（工作区/today_entries.json）

功能：
  1. 读取今日新增条目
  2. 校验时区与发布状态
  3. 按 news_id / 完整标题去重后追加
  4. 删除超 48 小时的记录
  5. 原子写回；历史文件损坏时拒绝覆盖
  6. 输出统计报告
"""

import json
import sys
import os
import tempfile
from datetime import datetime, timezone, timedelta


def parse_aware_timestamp(value):
    """解析带时区 ISO 时间；无时区时间拒绝参与比较。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp 为空")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp 缺少时区")
    return parsed.astimezone(timezone.utc)


def dedupe_keys(entry):
    if not isinstance(entry, dict):
        return set()
    keys = set()
    news_id = str(entry.get("news_id") or "").strip()
    if news_id:
        keys.add(("news_id", news_id))
    title = "".join(str(entry.get("title") or "").split()).lower()
    if title:
        keys.add(("title", title))
    return keys


def main():
    if len(sys.argv) < 3:
        print("用法: python3 update_history.py <history_json> <today_entries_json>")
        sys.exit(1)

    history_path = sys.argv[1]
    today_path = sys.argv[2]

    # ── 读取今日新增条目 ─────────────────────────────────────────────────
    if not os.path.exists(today_path):
        print(f"❌ 找不到今日条目文件：{today_path}")
        sys.exit(1)

    with open(today_path, 'r', encoding='utf-8') as f:
        try:
            today_entries = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ 今日条目文件 JSON 格式有误：{e}")
            sys.exit(1)

    if not isinstance(today_entries, list):
        print("❌ 今日条目文件必须是 JSON 数组")
        sys.exit(1)

    today_errors = []
    for i, entry in enumerate(today_entries, 1):
        if not isinstance(entry, dict):
            today_errors.append(f"第{i}条不是对象")
            continue
        if not str(entry.get("news_id") or "").strip():
            today_errors.append(f"第{i}条缺少 news_id")
        if not str(entry.get("title") or "").strip():
            today_errors.append(f"第{i}条缺少完整 title")
        if entry.get("publication_status") != "ready":
            today_errors.append(f"第{i}条 publication_status 不是 ready")
        try:
            parse_aware_timestamp(entry.get("timestamp"))
        except (ValueError, TypeError) as exc:
            today_errors.append(f"第{i}条 timestamp 无效：{exc}")
    if today_errors:
        print("❌ 今日条目校验失败，历史记录未修改：")
        for error in today_errors:
            print(f"  · {error}")
        sys.exit(1)

    # ── 读取历史记录（不存在则创建空列表）──────────────────────────────
    if os.path.exists(history_path):
        with open(history_path, 'r', encoding='utf-8') as f:
            try:
                history = json.load(f)
                if not isinstance(history, list):
                    print("❌ 历史记录文件内容不是数组；为保护原数据，拒绝覆盖")
                    sys.exit(1)
            except json.JSONDecodeError as exc:
                print(f"❌ 历史记录文件 JSON 格式有误；为保护原数据，拒绝覆盖：{exc}")
                sys.exit(1)
    else:
        history = []

    old_count = len(history)

    # ── 追加并去重：同一 news_id / 完整标题保留最后出现的新版本 ──────────
    combined = history + today_entries
    seen = set()
    deduped_reversed = []
    duplicate_count = 0
    for entry in reversed(combined):
        keys = dedupe_keys(entry)
        if keys and any(key in seen for key in keys):
            duplicate_count += 1
            continue
        seen.update(keys)
        deduped_reversed.append(entry)
    history = list(reversed(deduped_reversed))

    # ── 删除超 48 小时的记录 ────────────────────────────────────────────
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    kept = []
    pruned = 0
    malformed = 0

    for entry in history:
        if not isinstance(entry, dict):
            kept.append(entry)
            malformed += 1
            continue
        try:
            ts = parse_aware_timestamp(entry.get("timestamp"))
            if ts >= cutoff:
                kept.append(entry)
            else:
                pruned += 1
        except (ValueError, TypeError, AttributeError):
            # 2026-09-08：异常条目不再永久滞留。原设计"保留不丢数据"导致无时间戳条目
            # 永不过期（实测19条里9条如此，且它们无法参与48h判定、白占话题池）。
            # 改为：给它盖一个当前时间戳，使其在7天后被正常老化清出。
            malformed += 1
            first_seen = entry.get("_first_seen")
            if first_seen is None:
                entry["_first_seen"] = datetime.now(timezone.utc).isoformat()
                kept.append(entry)
            else:
                try:
                    fs = datetime.fromisoformat(str(first_seen).replace("Z", "+00:00"))
                    if fs >= datetime.now(timezone.utc) - timedelta(days=7):
                        kept.append(entry)
                    else:
                        pruned += 1   # 超7天，清出
                except Exception:
                    pruned += 1

    # ── 原子写回（先写临时文件，再重命名）──────────────────────────────
    dir_path = os.path.dirname(os.path.abspath(history_path))
    with tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', dir=dir_path,
        suffix='.tmp', delete=False
    ) as tmp:
        json.dump(kept, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name

    os.replace(tmp_path, history_path)

    # ── 输出统计报告 ─────────────────────────────────────────────────────
    print(f"\n✅ 历史记录更新完成")
    print(f"  原有记录：{old_count} 条")
    print(f"  新增：    {len(today_entries)} 条")
    print(f"  去重：    {duplicate_count} 条")
    print(f"  清理（>48h）：{pruned} 条")
    if malformed:
        print(f"  时间戳格式异常（已保留）：{malformed} 条")
    print(f"  当前保留：{len(kept)} 条")


if __name__ == "__main__":
    main()
