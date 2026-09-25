#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已发布视频加入合集：合集 tab → 早7点财经快报 编辑 → 添加视频 → 勾选匹配标题 → 确认。
用法: python3 sph_add_to_album.py "<合集名>" "<标题关键词1>" "<关键词2>" ... [--confirm]
不带 --confirm 只勾选+截图不保存（_sph_picker.png）。"""
import os, sys, json, time
from playwright.sync_api import sync_playwright

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
PROFILE = os.path.join(UPLOAD_ROOT, "config", "sph_profile")
SHOT = os.path.join(UPLOAD_ROOT, "物料")

COL = sys.argv[1]
do_confirm = "--confirm" in sys.argv
keys = [a for a in sys.argv[2:] if not a.startswith("--")]

with sync_playwright() as p:
    try:
        ctx = p.chromium.launch_persistent_context(user_data_dir=PROFILE, channel="chrome",
              headless=False, viewport={"width":1440,"height":900})
    except Exception:
        ctx = p.chromium.launch_persistent_context(user_data_dir=PROFILE, headless=False,
              viewport={"width":1440,"height":900})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://channels.weixin.qq.com/platform/post/list", wait_until="domcontentloaded")
    time.sleep(6)
    if "login" in page.url:
        print("LOGIN_NEEDED"); time.sleep(3); ctx.close(); sys.exit(1)

    page.get_by_text("合集 (5)", exact=False).first.click(timeout=5000)
    # 等行渲染
    for _ in range(15):
        time.sleep(1.5)
        if page.evaluate("""(n)=>[...document.querySelectorAll('*')].some(e=>(e.textContent||'').trim()===n && e.children.length===0)""", COL):
            break
    # 第一行（早7点财经快报）的编辑——若合集名非首行，按名定位其同行编辑
    page.get_by_text("编辑", exact=True).first.click(timeout=5000)
    time.sleep(5)
    print("合集编辑页:", page.url, flush=True)

    page.get_by_text("添加视频", exact=True).first.click(timeout=6000)
    time.sleep(3)
    page.screenshot(path=os.path.join(SHOT,"_sph_picker_open.png"), full_page=True)

    def counter():
        return page.evaluate(r"""() => { const b=[...document.querySelectorAll('button,[role=button]')].find(e=>/^添加\(\d+\)$/.test((e.innerText||'').trim())); return b?b.innerText.trim():'?'; }""")

    # 逐个：搜索框输入关键词 → 等结果 → 点结果条目 → 验证计数 +1
    for k in keys:
        try:
            box = page.locator("input[placeholder*='搜索视频']")
            box.fill("")
            box.fill(k)
            time.sleep(2.5)
            before = counter()
            r = page.evaluate(r"""(k) => {
              // 结果区里含关键词的条目，点其可点祖先（卡片/复选）
              const leaf=[...document.querySelectorAll('*')].find(e=>(e.textContent||'').includes(k) && e.children.length<=3
                 && !/搜索视频|仅展示/.test(e.textContent||''));
              if(!leaf) return 'NOTFOUND';
              let row=leaf;
              for(let i=0;i<6 && row.parentElement;i++){
                const cb=row.querySelector('input[type=checkbox], .ant-checkbox, [class*=checkbox]');
                if(cb){ cb.click(); return 'CHECKED-cb'; }
                row=row.parentElement;
              }
              row.click(); return 'CLICKED-row';
            }""", k)
            time.sleep(1)
            print(f"  {k}: {r} | 计数 {before}→{counter()}", flush=True)
        except Exception as e:
            print(f"  {k}: ERR {e}", flush=True)
    # 清空搜索框，截图全勾选态
    try:
        page.locator("input[placeholder*='搜索视频']").fill("")
    except Exception:
        pass
    time.sleep(1.5)
    print("最终计数:", counter(), flush=True)
    page.screenshot(path=os.path.join(SHOT,"_sph_picker.png"), full_page=True)

    if do_confirm:
        r = page.evaluate(r"""() => {
          const b=[...document.querySelectorAll('button,[role=button],a')].find(e=>/^(确定|确认|完成|保存|添加)$/.test((e.innerText||'').trim()));
          if(!b) return 'no-confirm'; b.click(); return 'confirmed:'+b.innerText.trim();
        }""")
        print("确认:", r, flush=True)
        time.sleep(4)
        page.screenshot(path=os.path.join(SHOT,"_sph_after_add.png"), full_page=True)
    else:
        print("STOP 未确认（看 _sph_picker.png 勾对没；加 --confirm 才保存）", flush=True)
    time.sleep(8)
    ctx.close()
