#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sph_open.py — 一次性打开微信视频号创作后台让用户扫码登录；cookie 持久化到 sph_profile。
检测：登录页是 login.html，登录后跳到 /platform/... → 用 URL 是否离开 login 判定（可靠）。

用法: python3 sph_open.py   （无参数；弹出浏览器，扫码登录后自动保存登录态到 $CAIJING_UPLOAD_HOME/config/）
"""
import os, time
from playwright.sync_api import sync_playwright

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")
PROFILE = os.path.join(UPLOAD_ROOT, "config", "sph_profile")
SHOT = os.path.join(UPLOAD_ROOT, "物料", "_sph_state.png")


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
        page.goto("https://channels.weixin.qq.com/platform", wait_until="domcontentloaded")
        time.sleep(6)  # 等重定向到 login.html
        try:
            page.screenshot(path=SHOT)
        except Exception:
            pass
        print("URL:", page.url, "| 请用【微信】扫码登录（窗口会一直开着等你）…", flush=True)
        logged = False
        for i in range(100):  # 约 300s
            time.sleep(3)
            url = page.url
            if "channels.weixin.qq.com" in url and "login" not in url:
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
