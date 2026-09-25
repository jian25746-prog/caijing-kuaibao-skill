# -*- coding: utf-8 -*-
"""
下载源模板 —— 复制本文件、改名（不要以下划线开头，如 mysite.py），按注释填写即可生效。
文件名以下划线开头时不会被加载，所以本模板本身永远不会生效。

完整说明见同目录 README.md。改完后用下面两条命令自检：
    python3 restock.py sources                       # 看新下载源是否出现、是否"可用"
    python3 restock.py search "关键词" --source mysite  # 试搜一次
"""
import subprocess

from .base import Candidate, Source


class MySource(Source):
    # ── 1. 基本信息 ─────────────────────────────────────────────
    name = ""                 # 必填：命令行里用的名字，小写字母/数字，如 "mysite"。留空则不加载
    display_name = "我的素材站"
    homepage = "https://example.com"
    env_key = ""              # 需要 API 密钥时填环境变量名，如 "MYSITE_API_KEY"；不需要就留空
    signup_url = ""           # 申请密钥 / 接口文档地址，没配密钥时会提示给用户
    license = ""              # 该站素材的授权名称
    license_url = ""
    notes = "使用限制摘要，例如：每小时限 N 次请求；禁止批量下载；须注明出处"

    # ── 2. 搜索：返回 Candidate 列表 ─────────────────────────────
    def search(self, query, n, portrait_only):
        """
        query          搜索词
        n              最多返回几条
        portrait_only  True 时只要竖屏；网站能按方向筛选就传参数，不能就拿到结果后按宽高过滤

        写法一（网站有 JSON 搜索接口）：
            data = self.get_json("https://example.com/api/search",
                                 params={"q": query, "limit": n},
                                 headers={"Authorization": self.api_key()})
            ↑ get_json 自带 24 小时缓存，同样的搜索不会重复请求接口
            然后把 data 里的每一条转成 Candidate（见下方示例）

        写法二（调用命令行工具列出搜索结果）：
            out = subprocess.run(["某个命令行工具", "搜索参数", query],
                                 capture_output=True, text=True, timeout=300).stdout
            逐行解析 out，转成 Candidate
        """
        results = []
        # 示例：把一条搜索结果转成 Candidate（字段含义见 base.py）
        # results.append(Candidate(
        #     source=self.name,
        #     id="12345",                               # 该站内唯一ID，用于去重
        #     title="city skyline at night",            # 终端里展示的简短描述
        #     page_url="https://example.com/video/12345",  # 素材页面，会记进出处台账
        #     download_url="https://example.com/files/12345.mp4",
        #     width=1080, height=1920, duration=12.0,
        #     author="作者名", author_url="https://example.com/user/xx",
        #     license=self.license, license_url=self.license_url,
        # ))
        if portrait_only:
            results = [c for c in results if c.vertical]
        return results[:n]

    # ── 3. 下载（可选）───────────────────────────────────────────
    # 不写这个函数时，默认直接用 HTTP 下载 Candidate.download_url。
    # 如果网站需要签名链接、登录，或者要调用命令行工具下载，就覆盖它：
    #
    # def download(self, cand, dest_path):
    #     subprocess.run(["某个命令行工具", cand.page_url, "-o", dest_path],
    #                    check=True, timeout=900)
    #     # 要求：函数结束时 dest_path 必须是一个完整的 mp4 文件，失败就抛异常
