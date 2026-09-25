#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_slices.py — caijing_slices.json 格式校验与自动修复

用法:
  python3 validate_slices.py <caijing_slices.json 路径>

自动修复纯格式问题，并对事实来源、新闻身份和发布状态执行硬门禁。
存在事实层错误时返回码 2，调用方必须停止渲染与发布。
"""

import json
import os
import sys
import re
import tempfile
from urllib.parse import urlparse

# ── 常量 ─────────────────────────────────────────────────────────────────
BOTTOM_PREFIXES = ["👉", "⚠️", "📈"]
OPINION_SUFFIX  = "（仅为个人观点）"
OPINION_RE      = re.compile(
    r"[，,。；;]?\s*(?:仅为个人观点|以上仅供参考|不构成(?:任何)?投资建议|本内容仅供参考)"
    r"(?:[，,、；;]?\s*(?:市场有风险|投资(?:有风险|需谨慎)|入市须谨慎))*[。！？!?]?",
    re.UNICODE,
)
VALID_TEMPLATES = {"T1_暗夜财经", "T2_突破警报", "T3_冷静分析", "T4_凡人速报", "T5_利好时刻"}
BLOCK_MARKERS = ("[数据待核实]", "【数据待核实】", "URL_MISSING", "待补充", "（缺，需人工补写）")
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])[-+]?\d+(?:[,.]\d+)*(?:万亿|亿|万)?"
    r"(?:%|％|美元|亿元|万亿元|元|点|个基点|基点|倍|只|件|家|吨|桶|股|年|月|日)?"
)
CN_NUMBER_RE = re.compile(
    r"(?:[零〇○一二三四五六七八九两]+点[零〇○一二三四五六七八九两]+(?:万亿|亿|万)?"
    r"|[零〇○一二三四五六七八九两十百千万亿]{2,}"
    r"|[零〇○一二三四五六七八九两十百千万亿]+(?=%|％|美元|亿元|万亿元|元|点|个基点|基点|倍|只|件|家|吨|桶|股|年|月|日))"
)
CN_NUMBER_PROTECT = ("百分", "万象", "万能", "亿万", "千万别", "十分", "百倍", "万分")

# ── 修复函数 ──────────────────────────────────────────────────────────────

def fix_bottom_prefixes(item: dict, fixes: list, idx: int) -> None:
    """修复1/2/3: bottom 前三行前缀缺失"""
    bottom = item.get("bottom", [])
    if not isinstance(bottom, list):
        return
    changed = False
    for i, prefix in enumerate(BOTTOM_PREFIXES):
        if i >= len(bottom):
            break
        line = bottom[i]
        if not isinstance(line, str):
            continue
        if not line.startswith(prefix):
            # 尝试剥掉旧的错误前缀（可能有别的表情符号开头）
            stripped = line.lstrip("👉⚠️📈 \t")
            bottom[i] = f"{prefix} {stripped}"
            changed = True
            fixes.append(f"[{idx:02d}] bottom[{i}] 缺少前缀 {prefix}，已补")
    if changed:
        item["bottom"] = bottom


def fix_bottom_opinion_suffix(item: dict, fixes: list, idx: int) -> None:
    """修复3b: bottom[2] 末尾须含「仅为个人观点」"""
    bottom = item.get("bottom", [])
    if len(bottom) < 3:
        return
    line = bottom[2]
    if isinstance(line, str) and OPINION_SUFFIX not in line:
        bottom[2] = line + OPINION_SUFFIX
        fixes.append(f"[{idx:02d}] bottom[2] 缺少末尾「仅为个人观点」，已补")


def fix_emotion_score(item: dict, fixes: list, idx: int) -> None:
    """修复5: emotion_score 字段缺失"""
    if item.get("emotion_score") is None:
        detail = item.get("emotion_detail") or {}
        calc = sum([
            detail.get("落地感", 0) or 0,
            detail.get("凡人立场", 0) or 0,
            detail.get("阴谋论解构", 0) or 0,
        ])
        item["emotion_score"] = calc if calc > 0 else 5
        fixes.append(f"[{idx:02d}] emotion_score 缺失，从 emotion_detail 计算为 {item['emotion_score']}")


def fix_source_url(item: dict, verification_entry: dict, fixes: list, idx: int) -> None:
    """仅在 news_id 已精确匹配时回填 source_url，禁止按数组位置猜测。"""
    url = item.get("source_url", "")
    if not url or url == "null" or url == "URL_MISSING":
        if verification_entry:
            real_url = verification_entry.get("source_url") or verification_entry.get("实际读取页URL", "")
            if real_url and real_url != "URL_MISSING":
                item["source_url"] = real_url
                fixes.append(f"[{idx:02d}] source_url 按 news_id 从 verification_table 精确回填: {real_url[:60]}")
                return


def fix_cn_source_url(item: dict, fixes: list, idx: int) -> None:
    """修复7: cn_source_url 字段缺失（不是空值，而是整个字段不存在）"""
    if "cn_source_url" not in item:
        item["cn_source_url"] = None
        fixes.append(f"[{idx:02d}] cn_source_url 字段缺失，已补 null")


def fix_narration_disclaimer(item: dict, fixes: list, idx: int) -> None:
    """修复8: narration 结尾含免责短语，自动删除"""
    narration = item.get("narration", "")
    if not isinstance(narration, str):
        return
    cleaned = OPINION_RE.sub("", narration).strip()
    if cleaned != narration:
        item["narration"] = cleaned
        fixes.append(f"[{idx:02d}] narration 末尾含免责短语，已删除")


TTS_MARK_RE = re.compile(r"<#[0-9.]+#>|清嗓声")


def fix_tts_marks(item: dict, fixes: list, idx: int) -> None:
    """修复10: hook/narration/bottom/first_comment 含停顿符/录音标记，自动剥除"""
    changed = []
    for sfield in ("narration", "first_comment"):
        v = item.get(sfield, "")
        if isinstance(v, str) and TTS_MARK_RE.search(v):
            item[sfield] = TTS_MARK_RE.sub("", v).strip()
            changed.append(sfield)
    for field in ("hook", "bottom"):
        lines = item.get(field, [])
        if isinstance(lines, list):
            hit = False
            cleaned = []
            for line in lines:
                if isinstance(line, str) and TTS_MARK_RE.search(line):
                    line = TTS_MARK_RE.sub("", line).strip()
                    hit = True
                cleaned.append(line)
            if hit:
                item[field] = cleaned
                changed.append(field)
    if changed:
        fixes.append(f"[{idx:02d}] {'/'.join(changed)} 含停顿符/录音标记，已剥除")


def check_hook_empty(item: dict, warnings: list, idx: int) -> None:
    """警告（不自动修复）：hook 为空或所有行均为空字符串"""
    hook = item.get("hook", [])
    if not hook or all(not str(h).strip() for h in hook):
        warnings.append(f"[{idx:02d}] hook 为空")


def _normalise_number(value: str) -> str:
    return value.replace(",", "").replace("％", "%").replace(" ", "")


def _content_text(item: dict) -> str:
    chunks = []
    for field in ("hook", "bottom"):
        value = item.get(field)
        if isinstance(value, list):
            chunks.extend(str(v) for v in value)
    for field in ("narration", "first_comment"):
        value = item.get(field)
        if isinstance(value, str):
            chunks.append(value)
    return "\n".join(chunks)


def _contains_cn_number(text: str) -> bool:
    """识别渲染前应改成白名单阿拉伯数字的中文数量表达；排除常见非数字词组。"""
    protected = text
    for phrase in CN_NUMBER_PROTECT:
        protected = protected.replace(phrase, "")
    return bool(CN_NUMBER_RE.search(protected))


def _is_specific_article_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return bool(parsed.netloc and path and path not in ("/news", "/business"))


def check_publication_gate(
    item: dict, verification_entry: dict, selected_entry: dict, errors: list, idx: int
) -> None:
    """验证不可自动猜测的发布条件。"""
    news_id = item.get("news_id")
    if not news_id:
        errors.append(f"[{idx:02d}] 缺少 news_id，无法核对新闻身份")

    if not isinstance(item.get("序号"), int) or item.get("序号") != idx:
        errors.append(f"[{idx:02d}] 序号必须是按切片顺序排列的整数 {idx}")

    if not selected_entry:
        errors.append(f"[{idx:02d}] selected_10 无对应 news_id")
    else:
        selected_title = selected_entry.get("标题") or selected_entry.get("title")
        if item.get("title") != selected_title:
            errors.append(f"[{idx:02d}] title 与同一 news_id 的 selected_10 标题不一致")
        if selected_entry.get("序号") != idx:
            errors.append(f"[{idx:02d}] selected_10 序号必须为 {idx}")
        if item.get("source_url") != selected_entry.get("source_url"):
            errors.append(f"[{idx:02d}] source_url 与同一 news_id 的 selected_10 不一致")

    hook = item.get("hook")
    if not isinstance(hook, list) or len(hook) != 4 or any(not isinstance(x, str) or not x.strip() for x in hook):
        errors.append(f"[{idx:02d}] hook 必须恰好4行且每行非空")
    elif any(_contains_cn_number(line) for line in hook):
        errors.append(f"[{idx:02d}] hook 含中文数字表达，必须在写稿层按 allowed_numbers 改为阿拉伯数字")
    bottom = item.get("bottom")
    if not isinstance(bottom, list) or len(bottom) < 3 or any(not isinstance(x, str) or not x.strip() for x in bottom[:3]):
        errors.append(f"[{idx:02d}] bottom 必须至少3行且前三行非空")
    for field in ("narration", "first_comment", "title_banner", "title"):
        if not isinstance(item.get(field), str) or not item.get(field, "").strip():
            errors.append(f"[{idx:02d}] {field} 缺失或为空")
    if item.get("template") not in VALID_TEMPLATES:
        errors.append(f"[{idx:02d}] template 无效：{item.get('template')!r}")

    if not verification_entry:
        errors.append(f"[{idx:02d}] verification_table 无对应 news_id")
        return

    if verification_entry.get("publication_status") != "ready":
        errors.append(
            f"[{idx:02d}] publication_status={verification_entry.get('publication_status')!r}，禁止发布"
        )
    if verification_entry.get("verification_status") not in ("verified", "partial"):
        errors.append(f"[{idx:02d}] verification_status 不是 verified/partial")
    excerpt = verification_entry.get("source_excerpt")
    if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 140:
        errors.append(f"[{idx:02d}] source_excerpt 必须为1-140字的原文片段")
    facts = verification_entry.get("verified_facts")
    if not isinstance(facts, list) or not facts or any(not isinstance(v, str) or not v.strip() for v in facts):
        errors.append(f"[{idx:02d}] verified_facts 必须是非空字符串数组")
    elif sum(len(v) for v in facts) > 120:
        errors.append(f"[{idx:02d}] verified_facts 合计超过120字token预算")
    evidence = verification_entry.get("evidence")
    if not isinstance(evidence, list) or not evidence or len(evidence) > 2:
        errors.append(f"[{idx:02d}] evidence 必须有1-2项")
    if not isinstance(verification_entry.get("allowed_numbers"), list):
        errors.append(f"[{idx:02d}] allowed_numbers 必须是数组")
    forbidden_facts = verification_entry.get("forbidden_facts", [])
    if not isinstance(forbidden_facts, list):
        errors.append(f"[{idx:02d}] forbidden_facts 存在时必须是数组")
        forbidden_facts = []

    source_url = item.get("source_url", "")
    verified_url = verification_entry.get("source_url") or verification_entry.get("实际读取页URL", "")
    if not _is_specific_article_url(source_url):
        errors.append(f"[{idx:02d}] source_url 不是具体文章URL：{source_url!r}")
    if not _is_specific_article_url(verified_url):
        errors.append(f"[{idx:02d}] verification_table source_url 不是具体文章URL：{verified_url!r}")
    elif source_url != verified_url:
        errors.append(f"[{idx:02d}] source_url 与同一 news_id 的核查URL不一致")

    content = _content_text(item)
    for marker in BLOCK_MARKERS:
        if marker in content or marker == source_url:
            errors.append(f"[{idx:02d}] 含发布阻断占位符：{marker}")

    for forbidden in forbidden_facts:
        if isinstance(forbidden, str) and forbidden.strip() and forbidden.strip() in content:
            errors.append(f"[{idx:02d}] 使用了 forbidden_facts：{forbidden[:40]}")

    allowed = {
        _normalise_number(str(v)) for v in (verification_entry.get("allowed_numbers") or []) if str(v).strip()
    }
    found = {_normalise_number(v) for v in NUMBER_RE.findall(content)}
    unexpected = sorted(found - allowed)
    if unexpected:
        errors.append(f"[{idx:02d}] 出现未列入 allowed_numbers 的数字：{', '.join(unexpected)}")


def export_first_comments(slices: list, slices_path: str):
    """导出 首条评论.txt 到 （日期）/ 目录（工作区上级），供发布切片后逐条复制贴评论区。
    返回 (输出路径, 非空评论条数)。"""
    work_dir = os.path.dirname(os.path.abspath(slices_path))   # （日期）/工作区
    day_dir = os.path.dirname(work_dir)                        # （日期）
    out_path = os.path.join(day_dir, "首条评论.txt")
    blocks, n_ok = [], 0
    for i, item in enumerate(slices, 1):
        if not isinstance(item, dict):
            continue
        fc = (item.get("first_comment") or "").strip()
        if fc:
            n_ok += 1
        hook = item.get("hook", [])
        tag = (hook[0] if isinstance(hook, list) and hook else "").strip()[:14]
        seq = item.get("序号") or i
        blocks.append(f"【第{int(seq):02d}条 {tag}】\n{fc}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")
    return out_path, n_ok


# ── 主函数 ────────────────────────────────────────────────────────────────

def load_verification_map(slices_path: str):
    """加载 verification_table.json，返回 ({news_id: entry}, errors)；禁止位置回退。"""
    vpath = os.path.join(os.path.dirname(slices_path), "verification_table.json")
    if not os.path.exists(vpath):
        return {}, ["verification_table.json 不存在"]
    try:
        with open(vpath, encoding="utf-8") as f:
            table = json.load(f)
        if not isinstance(table, list):
            return {}, ["verification_table.json 顶层不是数组"]
        result = {}
        errors = []
        for i, entry in enumerate(table, 1):
            if not isinstance(entry, dict) or not entry.get("news_id"):
                errors.append(f"verification_table 第{i}条缺少news_id")
                continue
            news_id = entry["news_id"]
            if news_id in result:
                errors.append(f"verification_table news_id重复：{news_id}")
            result[news_id] = entry
        return result, errors
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"verification_table.json 读取失败：{exc}"]


def load_selected_map(slices_path: str):
    """加载selected_10.json，返回({news_id: entry}, errors)；用于三方身份核对。"""
    spath = os.path.join(os.path.dirname(slices_path), "selected_10.json")
    if not os.path.exists(spath):
        return {}, ["selected_10.json 不存在"]
    try:
        with open(spath, encoding="utf-8") as f:
            selected = json.load(f)
        if not isinstance(selected, list):
            return {}, ["selected_10.json 顶层不是数组"]
        result = {}
        errors = []
        for i, entry in enumerate(selected, 1):
            if not isinstance(entry, dict) or not entry.get("news_id"):
                errors.append(f"selected_10 第{i}条缺少news_id")
                continue
            news_id = entry["news_id"]
            if news_id in result:
                errors.append(f"selected_10 news_id重复：{news_id}")
            result[news_id] = entry
        return result, errors
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"selected_10.json 读取失败：{exc}"]


def main():
    if len(sys.argv) < 2:
        print("用法: python3 validate_slices.py <caijing_slices.json>")
        sys.exit(1)

    path = sys.argv[1]

    if not os.path.exists(path):
        print(f"❌ 文件不存在：{path}")
        sys.exit(1)

    # 读取
    with open(path, encoding="utf-8") as f:
        try:
            slices = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ JSON 解析失败：{e}")
            sys.exit(1)

    if not isinstance(slices, list):
        print("❌ 文件顶层必须是 JSON 数组")
        sys.exit(1)

    fixes = []
    warnings = []
    verification_map, errors = load_verification_map(path)
    selected_map, selected_errors = load_selected_map(path)
    errors.extend(selected_errors)
    seen_ids = set()

    for i, item in enumerate(slices):
        idx = i + 1
        if not isinstance(item, dict):
            errors.append(f"[{idx:02d}] 条目不是对象")
            continue

        check_hook_empty(item, warnings, idx)
        fix_bottom_prefixes(item, fixes, idx)
        fix_bottom_opinion_suffix(item, fixes, idx)
        fix_emotion_score(item, fixes, idx)
        news_id = item.get("news_id")
        verification_entry = verification_map.get(news_id)
        selected_entry = selected_map.get(news_id)
        fix_source_url(item, verification_entry, fixes, idx)
        fix_cn_source_url(item, fixes, idx)
        fix_narration_disclaimer(item, fixes, idx)
        fix_tts_marks(item, fixes, idx)
        if news_id in seen_ids:
            errors.append(f"[{idx:02d}] news_id 重复：{news_id}")
        if news_id:
            seen_ids.add(news_id)
        check_publication_gate(item, verification_entry, selected_entry, errors, idx)

    ready_ids = {
        news_id for news_id, entry in verification_map.items()
        if entry.get("publication_status") == "ready"
    }
    non_ready_ids = sorted(set(verification_map) - ready_ids)
    if non_ready_ids:
        errors.append(f"verification_table 含非ready条目，应移入verification_rejections：{', '.join(non_ready_ids)}")
    if seen_ids != ready_ids:
        missing = sorted(ready_ids - seen_ids)
        extra = sorted(seen_ids - ready_ids)
        if missing:
            errors.append(f"切片缺少已核准 news_id：{', '.join(missing)}")
        if extra:
            errors.append(f"切片含未核准 news_id：{', '.join(extra)}")

    selected_ids = set(selected_map)
    if seen_ids != selected_ids:
        missing = sorted(selected_ids - seen_ids)
        extra = sorted(seen_ids - selected_ids)
        if missing:
            errors.append(f"切片缺少 selected_10 news_id：{', '.join(missing)}")
        if extra:
            errors.append(f"切片含 selected_10 之外 news_id：{', '.join(extra)}")

    banners = {item.get("title_banner") for item in slices if isinstance(item, dict)}
    if len(banners) != 1 or None in banners or "" in banners:
        errors.append("title_banner 必须全部一致且非空")

    # 原子写回
    dir_path = os.path.dirname(os.path.abspath(path))
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=dir_path, suffix=".tmp", delete=False
    ) as tmp:
        json.dump(slices, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, path)

    fc_msg = "  首条评论导出：已跳过（发布门禁未通过）"
    if not errors:
        try:
            fc_path, fc_n = export_first_comments(slices, path)
            fc_msg = f"  首条评论导出：{fc_n}/{len(slices)} 条 → {os.path.basename(fc_path)}"
        except Exception as e:
            errors.append(f"首条评论导出失败：{e}")

    # 报告
    print(f"\n✅ validate_slices 校验完成：共 {len(slices)} 条切片")
    if fixes:
        print(f"  自动修复：{len(fixes)} 项")
        for f in fixes:
            print(f"    · {f}")
    else:
        print("  自动修复：0 项（格式已完整）")
    if warnings:
        print(f"  需人工确认：{len(warnings)} 项")
        for w in warnings:
            print(f"    · {w}")
    if errors:
        print(f"  ❌ 发布门禁失败：{len(errors)} 项")
        for error in errors:
            print(f"    · {error}")
    print(fc_msg)
    print()
    if errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
