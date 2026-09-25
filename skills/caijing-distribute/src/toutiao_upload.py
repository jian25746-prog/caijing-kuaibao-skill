#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
toutiao_upload.py — 今日头条（头条号/西瓜）竖屏视频上传（Playwright，复用 tt_profile 登录态）。

⚠️ 竖屏(9:16)在头条的限制（2026-08-19 实测页面红字）：无原创声明、无赞赏、无定时发布。
表单很简单：标题(0~30字符, 必填) + 封面(必填) + 视频生成图文(默认开，按用户决定关掉) + 作品声明。
无描述/正文、无话题标签、发布时不能选合集。

文案取值：clip["toutiao"]["标题"] → 无则回退 clip["xiaohongshu"]["标题"] → 再回退 shipinhao.短标题。

用法: python3 toutiao_upload.py <节目> <日期> --clip 3 [--publish]
不带 --publish = 只填不发（截图 _tt_filled.png 供核对）。
"""
import os, sys, json, time
from playwright.sync_api import sync_playwright
from _waits import hpause, htype

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
PROFILE = os.path.join(UPLOAD_ROOT, "config", "tt_profile")
PUBLISH_URL = "https://mp.toutiao.com/profile_v4/xigua/upload-video"
SHOT_DIR = os.path.join(UPLOAD_ROOT, "物料")
TITLE_MAX = 30


def load_clips(show, date, only):
    cfg = json.load(open(os.path.join(UPLOAD_ROOT, "config", "shows.json"), encoding="utf-8"))
    shows = {k: v for k, v in cfg.items() if not k.startswith("_")}
    canon = show if show in shows else next((k for k, v in shows.items() if show in v.get("aliases", [])), show)
    mp = os.path.join(UPLOAD_ROOT, "物料", canon, date, "发布物料.json")
    clips = json.load(open(mp, encoding="utf-8"))["clips"]
    return [c for c in clips if (only is None or c["clip_num"] == only)]


def pick_title(clip):
    """头条标题：专用字段优先，否则复用小红书标题(≤20字，安全)，再否则视频号短标题。"""
    tt = clip.get("toutiao") or {}
    for cand in (tt.get("标题"),
                 (clip.get("xiaohongshu") or {}).get("标题"),
                 (clip.get("shipinhao") or {}).get("短标题")):
        if cand:
            return cand[:TITLE_MAX]
    return clip.get("真实内容", "")[:TITLE_MAX]


def launch(p):
    kwargs = dict(user_data_dir=PROFILE, headless=False, viewport={"width": 1440, "height": 900})
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception:
        return p.chromium.launch_persistent_context(**kwargs)


def main():
    show, date = sys.argv[1], sys.argv[2]
    only = int(sys.argv[sys.argv.index("--clip") + 1]) if "--clip" in sys.argv else None
    do_publish = "--publish" in sys.argv
    clips = load_clips(show, date, only)
    if not clips:
        print("✗ 没有 clip"); return
    clip = clips[0]
    video, cover = clip["video"], clip.get("cover")
    title = pick_title(clip)
    print(f"目标: 第{clip['clip_num']}条 · {clip.get('真实内容','')[:24]} | 发布={do_publish}", flush=True)
    print(f"标题({len(title)}/{TITLE_MAX}): {title}", flush=True)
    if len(title) > TITLE_MAX:
        print(f"⚠️ 标题超{TITLE_MAX}字，将被截断", flush=True)
    if not cover or not os.path.exists(cover):
        print(f"✗ 封面缺失（头条封面必填）: {cover}", flush=True); return

    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PUBLISH_URL, wait_until="domcontentloaded")
        time.sleep(6)
        if "login" in page.url or "auth/page" in page.url:
            print("LOGIN_NEEDED URL:", page.url, flush=True); time.sleep(3); ctx.close(); return
        print("LOGIN_OK", flush=True)

        # 1) 喂视频
        page.locator("input[type=file]").first.set_input_files(video)
        print("视频已喂入，等上传完成…", flush=True)
        uploaded = False
        for _ in range(60):  # 最多 ~3 分钟
            time.sleep(3)
            body = page.inner_text("body")
            if "上传成功" in body:
                uploaded = True; break
        if not uploaded:
            print("✗ 视频上传未确认（看截图）", flush=True)
            page.screenshot(path=os.path.join(SHOT_DIR, "_tt_filled.png"), full_page=True)
            ctx.close(); return
        print("✓ 视频上传成功", flush=True)
        hpause(1.0, 2.0)

        # 2) 标题（页面会自动填文件名，先清空再填）
        try:
            ti = page.locator("input[placeholder*='30 个字符'], input.xigua-input").first
            ti.click(); hpause(0.3, 0.7)
            page.keyboard.press("Meta+A"); page.keyboard.press("Backspace"); hpause(0.3, 0.6)
            htype(page, title)
            print("✓ 标题已填", flush=True)
        except Exception as e:
            print("标题失败:", e, flush=True)
        hpause(0.5, 1.0)

        # 3) 封面（必填）：点"上传封面" → 弹窗切到「本地上传」tab → 喂 PNG → 走完"下一步/确定"
        # 2026-08-19 实测：弹窗默认在「封面截取」(从视频截帧)，底部按钮是「下一步」，之后可能还有确认步骤
        try:
            page.get_by_text("上传封面", exact=False).first.click(timeout=5000)
            hpause(1.5, 2.5)
            # 切到本地上传 tab（用我们自己的封面，和其他平台保持一致）
            try:
                page.get_by_text("本地上传", exact=True).first.click(timeout=4000)
                print("  · 已切到「本地上传」", flush=True)
                hpause(1.0, 1.8)
            except Exception:
                print("  · 未找到「本地上传」tab，沿用默认截帧模式", flush=True)
            # 喂图：优先接受 image 的 input
            fis = page.locator("input[type=file]")
            idx = None
            for i in range(fis.count()):
                acc = (fis.nth(i).get_attribute("accept") or "").lower()
                if any(k in acc for k in ("image", "png", "jpg", "jpeg")):
                    idx = i; break
            if idx is None:
                idx = fis.count() - 1
            fis.nth(idx).set_input_files(cover)
            print(f"✓ 封面已喂入（input#{idx}）", flush=True)
            hpause(3.0, 4.5)
            # 走完弹窗按钮链：本地上传 →「封面编辑」页点"确定" → 二次确认"完成后无法继续编辑"再点"确定"
            # 2026-08-19 实测：至少两层，且两层的按钮都叫"确定"（同名多个 → 点最后一个＝最上层对话框）
            OPEN_MARKS = ("封面截取", "封面编辑", "是否确定完成")
            for step in range(6):
                try:
                    body = page.inner_text("body")
                except Exception:
                    body = ""
                if not any(m in body for m in OPEN_MARKS):
                    break  # 弹窗链已走完
                clicked = False
                for lab in ["确定", "下一步", "完成", "确认", "保存"]:
                    try:
                        b = page.get_by_role("button", name=lab)
                        if b.count() == 0:
                            b = page.get_by_text(lab, exact=True)
                        n = b.count()
                        if n > 0:
                            tgt = b.nth(n - 1)  # 同名多个时取最后一个（最上层对话框）
                            if tgt.is_visible():
                                tgt.click(timeout=3000)
                                print(f"  · 封面弹窗[{step+1}] 点了「{lab}」", flush=True)
                                clicked = True; hpause(2.0, 3.2); break
                    except Exception:
                        pass
                if not clicked:
                    print(f"  · 封面弹窗[{step+1}] 没找到可点按钮，停止", flush=True)
                    break
            # 真实校验：三个标记都不在页面上才算关干净
            try:
                body = page.inner_text("body")
                still = [m for m in OPEN_MARKS if m in body]
                print("  · 封面弹窗", f"仍开着 ⚠️{still}" if still else "已关闭 ✓", flush=True)
            except Exception:
                pass
        except Exception as e:
            print("封面处理异常:", e, flush=True)

        # 4) 关掉「生成图文」（用户 2026-08-19 决定：只发视频）
        try:
            r = page.evaluate("""() => {
              const lab=[...document.querySelectorAll('*')].find(e=>e.children.length===0 && (e.textContent||'').trim()==='生成图文');
              if(!lab) return 'no-label';
              let box=lab.parentElement;
              for(let i=0;i<4 && box;i++){
                const cb=box.querySelector('input[type=checkbox]');
                if(cb){ if(cb.checked){ cb.click(); return 'unchecked'; } return 'already-off'; }
                box=box.parentElement;
              }
              return 'no-checkbox';
            }""")
            print("生成图文:", r, flush=True)
        except Exception as e:
            print("生成图文处理异常:", e, flush=True)
        hpause(0.4, 0.9)

        # 5) 勾「投资观点，仅供参考」（用户 2026-08-19 决定）
        try:
            r = page.evaluate("""() => {
              const lab=[...document.querySelectorAll('*')].find(e=>e.children.length===0 && (e.textContent||'').includes('投资观点'));
              if(!lab) return 'no-label';
              let box=lab.parentElement;
              for(let i=0;i<4 && box;i++){
                const cb=box.querySelector('input[type=checkbox]');
                if(cb){ if(!cb.checked){ cb.click(); return 'checked'; } return 'already-on'; }
                box=box.parentElement;
              }
              return 'no-checkbox';
            }""")
            print("投资观点声明:", r, flush=True)
        except Exception as e:
            print("投资声明处理异常:", e, flush=True)
        hpause(0.5, 1.0)

        page.screenshot(path=os.path.join(SHOT_DIR, "_tt_filled.png"), full_page=True)
        print("截图: _tt_filled.png", flush=True)

        if not do_publish:
            print("STOP 只填不发（加 --publish 才真发）。窗口保留 12s", flush=True)
            time.sleep(12); ctx.close(); return

        # 6) 发布
        try:
            b = page.get_by_role("button", name="发布")
            if b.count() == 0:
                b = page.get_by_text("发布", exact=True)
            b.first.click(timeout=8000)
            print("发布: CLICKED", flush=True)
        except Exception as e:
            print("发布点击失败:", e, flush=True)
        hpause(6, 9)

        # 7) 真实核实：成功后通常跳离上传页（去内容管理），或出现成功提示
        ok = False
        for _ in range(10):
            time.sleep(3)
            u, body = page.url, ""
            try:
                body = page.inner_text("body")
            except Exception:
                pass
            if "upload-video" not in u or "发布成功" in body or "审核" in body:
                ok = True; break
        page.screenshot(path=os.path.join(SHOT_DIR, "_tt_published.png"), full_page=True)
        print(("PUBLISH_OK ✓ 已发布" if ok else "PUBLISH_FAIL ✗ 未确认（看 _tt_published.png）"),
              "| URL:", page.url, flush=True)
        time.sleep(2); ctx.close()


if __name__ == "__main__":
    main()
