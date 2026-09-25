#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tt_open.py — 一次性打开今日头条创作者后台让用户扫码登录；cookie 持久化到 tt_profile。
判据用 URL：登录页含 login/auth，登录后进 mp.toutiao.com 的工作台。
（沿用视频号那套：标准 Playwright + 独立 profile，与 sph/xhs 互不干扰）

用法: python3 tt_open.py   （无参数；弹出浏览器，扫码登录后自动保存登录态到 $CAIJING_UPLOAD_HOME/config/）
"""
import os, time
from playwright.sync_api import sync_playwright

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
PROFILE = os.path.join(UPLOAD_ROOT, "config", "tt_profile")
SHOT = os.path.join(UPLOAD_ROOT, "物料", "_tt_state.png")
HOME = "https://mp.toutiao.com/"


def launch(p):
    kwargs = dict(user_data_dir=PROFILE, headless=False,
                  viewport={"width": 1440, "height": 900})
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception as e:
        print(f"(real Chrome 失败，用 chromium: {e})", flush=True)
        return p.chromium.launch_persistent_context(**kwargs)


def main():
    os.makedirs(PROFILE, exist_ok=True)
    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(HOME, wait_until="domcontentloaded")
        time.sleep(6)
        try:
            page.screenshot(path=SHOT)
        except Exception:
            pass
        print("URL:", page.url, "| 若未登录请扫码/登录（窗口会一直开着等你）…", flush=True)
        logged = False
        for i in range(100):  # 约 300s
            time.sleep(3)
            u = page.url
            # 登录成功判据：留在 mp.toutiao.com 且不在登录/授权页
            if "mp.toutiao.com" in u and not any(k in u for k in ("login", "auth/page", "sso")):
                logged = True
                break
            if i % 6 == 0:
                try:
                    page.screenshot(path=SHOT)
                except Exception:
                    pass
        try:
            page.screenshot(path=SHOT)
        except Exception:
            pass
        print(("LOGIN_OK 登录成功，cookie 已保存" if logged else "LOGIN_TIMEOUT"), "| URL:", page.url, flush=True)
        time.sleep(1)
        ctx.close()


if __name__ == "__main__":
    main()
