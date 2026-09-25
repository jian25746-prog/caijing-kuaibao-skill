# -*- coding: utf-8 -*-
"""下载源统一接口。新增下载源只需继承 Source 并实现 search()，详见同目录 README.md。"""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict

UA = "caijing-broll-restock/1.0 (+https://github.com/jian25746-prog/caijing-kuaibao-skill)"
CACHE_TTL = 24 * 3600  # 部分平台（如 Pixabay）要求搜索结果缓存 24 小时


@dataclass
class Candidate:
    """一条可下载的候选视频。search() 必须返回它的列表。"""
    source: str           # 下载源名称，与 Source.name 一致
    id: str               # 该源内的唯一 ID（与 source 组合成全局去重键）
    title: str            # 简短描述，用于终端展示（可用标签/关键词拼接）
    page_url: str         # 素材在该网站上的页面地址（记录出处用）
    download_url: str     # 视频文件的直接下载地址
    width: int
    height: int
    duration: float       # 秒
    author: str = ""
    author_url: str = ""
    license: str = ""     # 授权名称，如 "Pexels License"
    license_url: str = ""

    @property
    def key(self):
        return f"{self.source}:{self.id}"

    @property
    def vertical(self):
        return self.height > self.width

    def to_dict(self):
        d = asdict(self)
        d["key"] = self.key
        return d


class Source:
    """下载源基类。子类至少设置 name，并实现 search()。"""

    name = ""            # 命令行里用的名字，只用小写字母/数字，如 "pexels"
    display_name = ""    # 展示名，如 "Pexels"
    homepage = ""        # 网站首页，展示出处时使用
    env_key = ""         # 需要的 API 密钥环境变量名；不需要密钥则留空
    signup_url = ""      # 申请密钥/阅读接口文档的地址，提示用户用
    license = ""
    license_url = ""
    notes = ""           # 该源的使用限制摘要，`restock.py sources` 会展示
    cache_dir = None     # 由 restock.py 注入：{LIBRARY}/_incoming/_search_cache

    def api_key(self):
        return os.environ.get(self.env_key, "").strip() if self.env_key else ""

    def available(self):
        """是否可用：默认只检查密钥是否已配置。"""
        return bool(self.api_key()) if self.env_key else True

    def search(self, query, n, portrait_only):
        """搜索并返回 [Candidate]。
        query: 搜索词；n: 最多返回条数；portrait_only: True 时只要竖屏（源不支持筛选就在本地按宽高过滤）。"""
        raise NotImplementedError

    def download(self, cand, dest_path):
        """把候选视频下载到 dest_path。默认直接 HTTP 下载 download_url；需要特殊处理（签名、登录）时覆盖。"""
        req = urllib.request.Request(cand.download_url, headers={"User-Agent": UA})
        tmp = dest_path + ".part"
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, dest_path)

    # ── 给子类用的工具方法 ─────────────────────────────────────
    def get_json(self, url, params=None, headers=None, cache=True):
        """GET 一个 JSON 接口；默认按 24 小时缓存（相同请求不重复打接口）。"""
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        cache_file = None
        cache_dir = self.cache_dir if cache else None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            h = hashlib.sha1((url + json.dumps(headers or {}, sort_keys=True)).encode()).hexdigest()[:16]
            cache_file = os.path.join(cache_dir, f"{self.name}_{h}.json")
            if os.path.exists(cache_file) and time.time() - os.path.getmtime(cache_file) < CACHE_TTL:
                with open(cache_file, encoding="utf-8") as f:
                    return json.load(f)
        req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        if cache_file:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        return data
