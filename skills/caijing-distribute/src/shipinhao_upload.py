#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shipinhao_upload.py — 微信视频号全自动上传（Playwright，复用 sph_profile 登录态）。
表单在 frame url 含 micro/content 的 iframe 里（字段是真 DOM）。
复用登录 → set_input_files 上传 → frame 内填描述/短标题 → 点声明原创 → 截图 →（--publish 才发表）。
用法: python3 shipinhao_upload.py <节目> <日期> --clip 9 [--publish] [--at HH:MM]
  --at HH:MM  定时发表（当天该时刻由平台自动发出，本机无需在线）。⚠️ 必须是未来时刻，
              否则控件静默钳制到"当前时刻"（实测填过去的 09:20 会变成 11:51）。
"""
import os, sys, json, time, re
from playwright.sync_api import sync_playwright
from _waits import hpause, htype

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
PROFILE = os.path.join(UPLOAD_ROOT, "config", "sph_profile")
PUBLISH_URL = "https://channels.weixin.qq.com/platform/post/create"
SHOT_DIR = os.path.join(UPLOAD_ROOT, "物料")


def load_clips(show, date, only):
    cfg = json.load(open(os.path.join(UPLOAD_ROOT, "config", "shows.json"), encoding="utf-8"))
    shows = {k: v for k, v in cfg.items() if not k.startswith("_")}
    canon = show if show in shows else next((k for k, v in shows.items() if show in v.get("aliases", [])), show)
    collection = shows.get(canon, {}).get("collection")
    mp = os.path.join(UPLOAD_ROOT, "物料", canon, date, "发布物料.json")
    clips = json.load(open(mp, encoding="utf-8"))["clips"]
    return [c for c in clips if (only is None or c["clip_num"] == only)], collection


def select_album(frame, name):
    """添加到合集：点开 .post-album-display → 在 .filter-wrap 选名字匹配的合集（单击=选中，开关式不可重复点）。
    ⚠️ 2026-06-30 教训：选项是开关，重复点会取消；且 display-text 读取不可靠（误报未选）。故只点一次、不靠 display 校验。
    可靠核实只能事后看合集页内容数（distribute 后用合集 tab 核对）。"""
    if not name:
        return "no-name"
    try:
        r = frame.evaluate("""() => {
          const d=document.querySelector('.post-album-display'); if(!d) return 'no-display';
          d.click(); return 'opened';
        }""")
        if r != "opened":
            return f"FAIL:{r}"
        time.sleep(1.5)
        r2 = frame.evaluate("""(name) => {
          const leaf=[...document.querySelectorAll('.filter-wrap *')]
            .find(e=>e.children.length===0 && (e.textContent||'').trim()===name);
          if(!leaf) return 'not-found';
          let row=leaf;
          for(let i=0;i<5 && row.parentElement;i++){ row=row.parentElement;
            if((row.className||'').includes('option')||(row.className||'').includes('item')) break; }
          row.click(); return 'clicked';
        }""", name)
        time.sleep(1.0)
        disp = frame.evaluate("() => { const t=document.querySelector('.post-album-display .display-text'); return t?t.textContent.trim():''; }")
        return ("OK clicked" if r2 == "clicked" else f"FAIL:{r2}") + f" display='{disp}'"
    except Exception as e:
        return f"FAIL:err {e}"


def launch(p):
    kwargs = dict(user_data_dir=PROFILE, headless=False, viewport={"width": 1440, "height": 900})
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception:
        return p.chromium.launch_persistent_context(**kwargs)


def _looks_like_form(ctx):
    """视频号后台会把发布表单放在不同容器里：
    - 老版：url 含 micro/content 的 iframe
    - 2026-09-11 实测：有时在主页面/非 micro iframe；继续只按 URL 找会导致视频已进页面但脚本一直等不到表单。
    """
    checks = [
        lambda: ctx.locator(".input-editor").count() > 0,
        lambda: ctx.locator("input[placeholder*='短标题']").count() > 0,
        lambda: ctx.get_by_text("视频描述", exact=False).count() > 0 and ctx.locator("input[type=file]").count() > 0,
    ]
    for check in checks:
        try:
            if check():
                return True
        except Exception:
            pass
    return False


def form_frame(page):
    # 优先老版 iframe，避免误扫到外层导航；若站点改版，再按真实表单特征兜底。
    frames = list(page.frames)
    for fr in frames:
        if "micro/content" in fr.url and _looks_like_form(fr):
            return fr
    for fr in frames:
        if _looks_like_form(fr):
            return fr
    if _looks_like_form(page):
        return page
    return None


def scroll_all_to_bottom(page, frame=None):
    """新版视频号页面把表单放在主页面滚动容器里，不一定是 document/iframe。
    发表按钮、原创声明在下方；只滚 document 会看不到也找不到。
    """
    js = """() => {
      window.scrollTo(0, document.body.scrollHeight);
      document.querySelectorAll('*').forEach(e => {
        try {
          if (e.scrollHeight > e.clientHeight + 20) e.scrollTop = e.scrollHeight;
        } catch (_) {}
      });
    }"""
    try:
        page.evaluate(js)
    except Exception:
        pass
    if frame and frame is not page:
        try:
            frame.evaluate(js)
        except Exception:
            pass


def click_publish_button(page, frame):
    """兼容老版 iframe 和 2026-09 新版主页面。
    老版按钮 innerText 恰好是「发表」；新版可能是「发布」/懒加载到底部后才出现。
    """
    scroll_all_to_bottom(page, frame)
    time.sleep(1.0)
    js = """() => {
      const wanted = ['发表', '发布', '定时发表'];
      const els = [...document.querySelectorAll('button,[role=button],.weui-desktop-btn,.ant-btn,span,div,a')]
        .filter(e => {
          const t = (e.innerText || e.textContent || '').trim();
          if (!wanted.some(w => t === w || t.includes(w))) return false;
          const b = e.getBoundingClientRect();
          const s = getComputedStyle(e);
          return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
        });
      if (!els.length) {
        return {ok:false, reason:'NF', buttons:[...document.querySelectorAll('button,[role=button],.weui-desktop-btn,.ant-btn')]
          .map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean).slice(0,30)};
      }
      const el = els[els.length - 1].closest('button,[role=button],.weui-desktop-btn,.ant-btn') || els[els.length - 1];
      el.scrollIntoView({block:'center'});
      el.click();
      return {ok:true, text:(el.innerText || el.textContent || '').trim()};
    }"""
    contexts = []
    if frame:
        contexts.append(("form", frame))
    contexts.append(("page", page))
    for fr in page.frames:
        if frame is not fr:
            contexts.append(("frame", fr))
    last = None
    for name, ctx in contexts:
        try:
            r = ctx.evaluate(js)
            last = (name, r)
            if isinstance(r, dict) and r.get("ok"):
                return f"CLICKED:{name}:{r.get('text')}"
        except Exception as e:
            last = (name, f"ERR:{e}")
    # 坐标兜底：新版底部橙色按钮通常在右下；只在 DOM 全失败后尝试一次。
    try:
        page.mouse.click(1195, 792)
        return f"CLICKED:coord:fallback last={last}"
    except Exception as e:
        return f"NF last={last} coord_err={e}"


def click_visible_text(page, frame, text, prefer_last=True):
    # ⚠️ 2026-09-12 实锤修复：原来写成 ctx.evaluate(js, text, prefer_last)——Playwright 的 evaluate
    # 只收 **一个** arg，多传会抛 TypeError，又被下面 except Exception 静默吞掉 → 这个函数一直返回 False、
    # 从未真正生效过（此前"能发"全靠坐标兜底，坐标一失效就 10 条全挂）。多参数必须打包成一个对象传。
    js = """(args) => {
      const {text, preferLast} = args;
      const cands = [...document.querySelectorAll('button,[role=button],.weui-desktop-btn,.ant-btn,span,div,a')]
        .filter(e => {
          const t = (e.innerText || e.textContent || '').trim();
          if (t !== text) return false;
          const b = e.getBoundingClientRect();
          const s = getComputedStyle(e);
          return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
        });
      if (!cands.length) return false;
      const raw = preferLast ? cands[cands.length - 1] : cands[0];
      const el = raw.closest('button,[role=button],.weui-desktop-btn,.ant-btn') || raw;
      el.scrollIntoView({block:'center'});
      el.click();
      return true;
    }"""
    contexts = []
    if frame:
        contexts.append(frame)
    contexts.append(page)
    contexts.extend([fr for fr in page.frames if fr is not frame])
    for ctx in contexts:
        try:
            if ctx.evaluate(js, {"text": text, "preferLast": prefer_last}):
                return True
        except Exception:
            pass
    return False


def page_has_text(page, text):
    for ctx in [page] + list(page.frames):
        try:
            if ctx.get_by_text(text, exact=False).count() > 0:
                return True
        except Exception:
            pass
    return False


def handle_publish_modals(page, frame):
    """发表后新版页面（2026-09 实测截图确认）会弹两层对话框，全部挂在主 page 层级（非 iframe 内）：
    ① 「声明原创的视频有机会获得广告分成」——三个按钮：不再提醒 / 直接发表 / 声明原创（橙色）。
    ② 点「声明原创」后弹「原创权益」——先勾"我已阅读并同意《原创声明须知》和《使用条款》"复选框，
       该框未勾时「声明原创」确认按钮是禁用态，勾选后才可点亮点击。
    按钮文字精确匹配，不再依赖坐标（2026-09-12 实测坐标点位已对不上、10 条全挂）。
    """
    events = []
    republish = 0          # 原创声明走完会回到表单，需要再点一次「发表」才真正提交
    for _round in range(12):
        time.sleep(1.2)
        if "post/list" in page.url:
            break
        if os.environ.get("SPH_DEBUG_MODAL"):
            # ⚠️ 不用 full_page（会卡 waiting for fonts 到 30s 超时，自己变成干扰源）
            try:
                page.screenshot(path=os.path.join(SHOT_DIR, f"_sph_modal_round{_round}.png"), timeout=5000)
            except Exception as e:
                print(f"  [DBG r{_round}] shot fail: {str(e)[:40]}", flush=True)
            # 弹窗容器内所有元素的完整 tag/class（不截断）——定位可点元素用
            for fi, ctx in enumerate([page] + list(page.frames)):
                try:
                    hits = ctx.evaluate("""() => {
                      const dlg=[...document.querySelectorAll('*')].find(e=>{
                        const t=(e.innerText||'').trim();
                        return t.startsWith('原创权益') && t.includes('我已阅读并同意')
                          && e.getBoundingClientRect().width>0 && e.getBoundingClientRect().width<900; });
                      if(!dlg) return null;
                      return [...dlg.querySelectorAll('*')].slice(0,25)
                        .map(e=>`${e.tagName}[${(e.className||'').toString()}]${e.disabled?'(disabled)':''}"${(e.innerText||'').trim().slice(0,10)}"`);
                    }""")
                    if hits:
                        print(f"  [DBG r{_round}] 原创权益弹窗 ctx{fi} DOM:", flush=True)
                        for h in hits:
                            print(f"      {h}", flush=True)
                except Exception:
                    pass

        # 🔴 顺序很关键：必须先判第二层。第二层「原创权益」弹窗里也有一个 innerText 同样是「声明原创」
        # 的确认按钮（未勾同意时是禁用态），若先判第一层会一直匹配到它、点了无效再 continue，
        # 永远走不到勾选复选框那步 → 10 轮空转（2026-09-12 实测死循环根因）。
        # 第二层：原创权益协议——先勾选框，再点亮的「声明原创」确认
        # ⚠️ 弹窗挂在 micro/content iframe 里（2026-09-12 全 frame 扫描实测），必须逐 context 找，
        #    只在主 page 上查 DOM 会一无所获。
        # 🔴 weui 的自绘复选框不吃 JS 的 .click()（2026-06-20 就踩过："DOM 点不动、须坐标点"），
        #    且 JS click 不报错 → 会返回假的 'checked' 把坐标兜底屏蔽掉（2026-09-12 实测截图：
        #    日志连报 10 次 checked，画面里框始终是空的、确认键始终灰）。
        #    正解：JS 只负责拿 rect，点击一律走真实鼠标，点完**验证**勾没勾上，没勾上才重试。
        # 用 Playwright 原生 locator 点击（自动处理 iframe 偏移/滚动/可见性等待）——
        # 2026-09-12 实测：自己拿 iframe 内 rect 再用 page 层坐标点击会偏，点了确认键依旧灰。
        # 2026-09-12 全 DOM dump 实测结构（弹窗在 micro/content iframe 内）：
        #   DIV[original-proto-wrapper] > LABEL[ant-checkbox-wrapper] > SPAN[ant-checkbox] > INPUT[ant-checkbox-input]
        #   底部 DIV[weui-desktop-dialog__ft] 里是「取消」「声明原创」两个 weui 按钮。
        # 即：复选框是标准 AntD Checkbox → 点 LABEL.ant-checkbox-wrapper 最稳。
        ctxs = ([frame] if frame else []) + [page] + [fr for fr in page.frames if fr is not frame]
        hit_ctx, clicked_how = None, None
        for ctx in ctxs:
            for sel in [".original-proto-wrapper .ant-checkbox-wrapper", ".ant-checkbox-wrapper"]:
                try:
                    loc = ctx.locator(sel)
                    if loc.count() > 0:
                        loc.last.click(timeout=4000)
                        hit_ctx, clicked_how = ctx, sel
                        break
                except Exception:
                    pass
            if hit_ctx:
                break
        if hit_ctx:
            box_rect = {"tag": "locator", "cls": clicked_how}
            time.sleep(0.6)
            # 验证：确认按钮是否已从禁用变为可点（这是"真的勾上了"的可观测信号）
            enabled = False
            for ctx in ([hit_ctx] if hit_ctx else []) + ctxs:
                try:
                    enabled = ctx.evaluate("""() => [...document.querySelectorAll('button,[role=button],.weui-desktop-btn')]
                        .some(e=>(e.innerText||'').trim()==='声明原创' && !e.disabled
                          && !(e.className||'').includes('disabled') && e.getBoundingClientRect().width>0)""")
                    if enabled:
                        break
                except Exception:
                    pass
            events.append(f"原创权益勾选[{box_rect['tag']}.{box_rect['cls']}]→{'确认键已点亮' if enabled else '仍灰'}")
            if not enabled:
                time.sleep(0.8)
                continue  # 没点亮就下一轮重试，别去点灰按钮空转
            # 确认按钮同样用 locator 点
            r2 = "NF"
            for ctx in ([hit_ctx] if hit_ctx else []) + ctxs:
                for sel in [".weui-desktop-dialog__ft button:not([disabled])",
                            "button:not([disabled])"]:
                    try:
                        btn = ctx.locator(sel).filter(has_text="声明原创").last
                        if btn.count() > 0:
                            btn.click(timeout=4000)
                            r2 = "CLICKED"
                            break
                    except Exception:
                        pass
                if r2 == "CLICKED":
                    break
            events.append(f"原创权益确认→{r2}")
            if r2 == "CLICKED":
                time.sleep(1.5)
            continue

        # 第一层：广告分成/声明原创 选择弹窗。
        # 🔴 必须限定在弹窗容器 .weui-desktop-dialog 内找，否则会误匹配表单自己的「声明原创」字段标签
        #    （DIV.label with-tip-label"声明原创"）→ 原地空点 10 轮（2026-09-12 实测）。
        clicked1 = False
        for ctx in ([frame] if frame else []) + [page] + [fr for fr in page.frames if fr is not frame]:
            try:
                r = ctx.evaluate("""() => {
                  const dlgs=[...document.querySelectorAll('.weui-desktop-dialog')]
                    .filter(d=>d.getBoundingClientRect().width>0);
                  for (const d of dlgs) {
                    const btn=[...d.querySelectorAll('button,[role=button],.weui-desktop-btn')]
                      .find(e=>(e.innerText||'').trim()==='声明原创' && e.getBoundingClientRect().width>0);
                    if (btn) { btn.click(); return true; }
                  }
                  return false;
                }""")
                if r:
                    clicked1 = True
                    break
            except Exception:
                pass
        if clicked1:
            events.append("弹窗1→声明原创")
            time.sleep(1.2)
            continue

        # 弹窗都没了、URL 还在 post/create ⇒ 原创声明已回填到表单，需要**再点一次发表**才真正提交
        # （2026-09-12 截图实证：此时表单「声明原创」已打勾、底部「发表」可点）。
        if republish < 2:
            republish += 1
            r = click_publish_button(page, frame)
            events.append(f"回表单→重点发表#{republish}:{r}")
            time.sleep(3.0)
            continue

        # 兜底：实在走不通就点「直接发表」，至少保证发得出去（牺牲原创标记）。
        if page_has_text(page, "直接发表"):
            if click_visible_text(page, frame, "直接发表", prefer_last=True):
                events.append("兜底→直接发表")
                continue
    return " | ".join(events) if events else "no-modal"


def main():
    show, date = sys.argv[1], sys.argv[2]
    only = int(sys.argv[sys.argv.index("--clip") + 1]) if "--clip" in sys.argv else None
    do_publish = "--publish" in sys.argv
    at = sys.argv[sys.argv.index("--at") + 1] if "--at" in sys.argv else None   # 定时发表 HH:MM（当天）
    clips, collection = load_clips(show, date, only)
    if not clips:
        print("✗ 没有 clip"); return
    clip = clips[0]
    sph = clip["shipinhao"]
    video = clip["video"]
    print(f"目标: 第{clip['clip_num']}条 · {clip.get('真实内容','')} | 发布={do_publish}", flush=True)

    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PUBLISH_URL, wait_until="domcontentloaded")
        time.sleep(6)
        if "login" in page.url:
            print("LOGIN_NEEDED URL:", page.url, flush=True); time.sleep(4); ctx.close(); return
        print("LOGIN_OK", flush=True)

        page.screenshot(path=os.path.join(SHOT_DIR, "_sph_initial.png"))
        nfi = page.locator("input[type=file]").count()
        print("初始 file inputs:", nfi, "| URL:", page.url, flush=True)
        if nfi == 0:
            print("✗ 无 file input（看 _sph_initial.png，可能有草稿/弹窗挡住）", flush=True)
            time.sleep(3); ctx.close(); return
        page.locator("input[type=file]").first.set_input_files(video)
        print("视频已喂入，等发布表单…", flush=True)
        frame = None
        for _ in range(40):
            time.sleep(3)
            frame = form_frame(page)
            if frame and frame.locator(".input-editor").count() > 0:
                break
        if not frame:
            print("✗ 未找到发布表单", flush=True); ctx.close(); return
        print(f"✓ 发布表单已识别: {getattr(frame, 'url', page.url)}", flush=True)

        # 等视频真正传完（描述框比上传进度先渲染，之前遇到"上传0%就点发表"导致 PUBLISH_FAIL——2026-09-04 实锤）
        # 判据：页面上"取消上传"按钮消失（上传中才有，传完自动隐藏）
        # 2026-09-12：180s 对偶发网络波动不够（第7条连挂两次，文件本身 ffprobe 验过完好）→ 放宽到 300s。
        # 正常条目传完即 break，不受影响。
        uploading = True
        for _ in range(100):
            try:
                still = frame.get_by_text("取消上传", exact=True).count() > 0
            except Exception:
                still = False
            if not still:
                uploading = False
                break
            time.sleep(3)
        if uploading:
            print("✗ 等待上传完成超时(300s)，停止本条，避免假发布", flush=True)
            ctx.close(); return
        print("✓ 视频上传完成", flush=True)

        # 关首次提示气泡（"我知道了"会遮挡表单导致点击超时）
        for fr in page.frames:
            for lab in ["我知道了", "知道了"]:
                try:
                    loc = fr.get_by_text(lab, exact=True)
                    if loc.count() > 0:
                        loc.first.click(timeout=2000); time.sleep(0.4)
                        print("· 关提示:", lab, flush=True)
                except Exception:
                    pass

        # 描述（contenteditable .input-editor）：JS 聚焦兜底，避免被遮挡点不动
        try:
            frame.locator(".input-editor").first.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass
        try:
            frame.locator(".input-editor").first.click(timeout=6000)
        except Exception:
            frame.evaluate("() => { const e=document.querySelector('.input-editor'); if(e) e.focus(); }")
        hpause(0.5, 0.9)
        htype(page, sph["描述"]); hpause(0.4, 0.9)
        for t in sph.get("话题", []):
            try:
                htype(page, " #" + t); hpause(1.0, 1.7)
                page.keyboard.press("Enter"); hpause(0.2, 0.5)
            except Exception as e:
                print(f"  (话题 {t} 失败: {e})", flush=True)
        print("✓ 描述+话题已填", flush=True)

        # 短标题兜底清洗（平台禁 ，。.%+?! 等特殊符，违规会静默发表失败——2026-06-24 实锤；报红字提示"可用空格代替"故替换为空格）
        _st_raw = sph["短标题"]
        _st = re.sub(r"[，。．.、；;：:？?！!％%＋+\"'“”‘’《》〈〉（）()\[\]【】—–~～·…|｜/\\]", " ", _st_raw)
        _st = re.sub(r"\s+", " ", _st).strip()
        if _st != _st_raw:
            print(f"  ⚠️ 短标题含特殊符已清洗: 「{_st_raw}」→「{_st}」（数字带符号的写法应在文案层改成纯文字，如 2.1万→两万一）", flush=True)
        if len(_st) > 16:
            print(f"  ⚠️ 短标题超16字({len(_st)}字)，平台可能拒绝，建议改短", flush=True)
        elif len(_st) < 6:
            print(f"  ⚠️ 短标题不足6字({len(_st)}字)，平台可能拒绝", flush=True)
        sph["短标题"] = _st

        # 短标题（frame.fill 超时则 JS 兜底，带 React 兼容的 input 事件）
        try:
            frame.fill("input[placeholder*='短标题']", sph["短标题"], timeout=6000)
            print("✓ 短标题:", sph["短标题"], flush=True)
        except Exception:
            try:
                frame.evaluate("""(v) => { const i=[...document.querySelectorAll('input')].find(x=>(x.placeholder||'').includes('短标题')); if(i){ const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(i,v); i.dispatchEvent(new Event('input',{bubbles:true})); } }""", sph["短标题"])
                print("✓ 短标题(JS):", sph["短标题"], flush=True)
            except Exception as e:
                print("短标题失败:", e, flush=True)

        # 添加到合集（shows.json.collection）
        print("合集:", select_album(frame, collection), flush=True)
        hpause(0.4, 0.8)

        # 滚到底（声明原创/发表 在底部；新版页面有内部滚动容器）
        scroll_all_to_bottom(page, frame)
        time.sleep(1.2)

        # 声明原创：2026-09 新版页面已移除表单内联勾选项，改为点「发表」后弹两层对话框
        # （①"声明原创的视频有机会获得广告分成"选择 ②"原创权益"勾同意确认），
        # 处理逻辑见 handle_publish_modals()，这里只探测一下、找不到就跳过，不再对空跑的坐标瞎点。
        try:
            r = frame.evaluate("""() => {
              const el=[...document.querySelectorAll('*')].find(e=>(e.textContent||'').includes('作品将展示原创') && e.children.length<=3);
              if(!el) return 'NF(新版已挪到发表后弹窗，正常)';
              const cb=el.closest('.ant-checkbox-wrapper') || el.querySelector('.ant-checkbox-input') || el;
              cb.click(); return 'CLICKED(老版表单内联)';
            }""")
            print("声明原创预勾选探测:", r, flush=True)
        except Exception as e:
            print("声明原创预勾选探测失败(不影响，走发表后弹窗):", e, flush=True)

        # 🔴 定时发表（--at HH:MM，当天）：2026-08-01 逐步实测出的配方，顺序/手法都不能改
        #   ① 「定时」单选用 JS 点可以生效
        #   ② 「发表时间」框必须用**真实鼠标坐标**点开（JS click 不弹层；iframe 偏移实测为 0,0）
        #   ③ 弹层内「时间」框：真实鼠标点 → Meta+A 全选 → keyboard.type("HH:MM")
        #      （原生 setter 赋值无效——weui 是受控组件；点时/分两列也不提交）
        #   ④ **点弹层外空白关闭弹层才真正提交**（不关则「发表时间」不更新）
        #   ⚠️ 时间必须在**未来**，否则控件静默钳制到"当前时刻"（实测填 09:20 变成 11:51）
        if at:
            try:
                frame.evaluate("""() => {const el=[...document.querySelectorAll('*')].find(e=>e.children.length<=1&&(e.textContent||'').trim()==='定时');
                    (el.closest('.ant-radio-wrapper')||el.closest('label')||el).click();}""")
                time.sleep(2)
                r = frame.evaluate("""() => {const d=[...document.querySelectorAll('input')].find(e=>(e.placeholder||'').includes('请选择发表时间'));
                    if(!d) return null; const b=d.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height};}""")
                if not r:
                    raise RuntimeError("未找到发表时间输入框")
                page.mouse.click(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
                time.sleep(2.5)
                t = frame.evaluate("""() => {const t=[...document.querySelectorAll('input')].find(e=>(e.placeholder||'').includes('请选择时间'));
                    if(!t) return null; const b=t.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height};}""")
                if not t:
                    raise RuntimeError("未找到时间输入框（弹层没开？）")
                page.mouse.click(t["x"] + t["w"] / 2, t["y"] + t["h"] / 2)
                time.sleep(0.8)
                page.keyboard.press("Meta+A")
                page.keyboard.type(at, delay=100)
                time.sleep(0.8)
                page.mouse.click(400, 700)      # 点空白关弹层 → 提交
                time.sleep(2)
                got = frame.evaluate("""() => {const d=[...document.querySelectorAll('input')].find(e=>(e.placeholder||'').includes('请选择发表时间'));
                    return d ? d.value : '';}""")
                ok_at = got.endswith(at)
                print(f"定时发表: 目标{at} → 实际「{got}」{'✓' if ok_at else '✗ 不符(可能已过当前时刻被钳制)'}", flush=True)
            except Exception as e:
                print(f"定时发表设置失败（将按立即发表处理）: {e}", flush=True)

        time.sleep(1)
        # ⚠️ 2026-08-15 实锤：full_page 截图会卡在"waiting for fonts to load"直到 30s 超时抛异常，
        # 由于它在「点发表」之前，一崩就导致整条根本没发出去（第9条连挂两次）。
        # 截图只是留证、不是发布必需 → 缩短超时 + 异常吞掉，绝不让它阻断发布。
        try:
            page.screenshot(path=os.path.join(SHOT_DIR, "_sph_filled.png"), full_page=True, timeout=8000)
            print("截图: _sph_filled.png", flush=True)
        except Exception as e:
            print(f"（填表截图失败，跳过不影响发布: {str(e)[:60]}）", flush=True)

        if not do_publish:
            print("STOP 只填不发（看截图：声明原创勾上没/弹没弹对话框）。窗口保留 10s", flush=True)
            time.sleep(10); ctx.close(); return

        # 发表：兼容老版 iframe / 新版主页面 / 懒加载底部按钮
        try:
            r = click_publish_button(page, frame)
            print("发表:", r, flush=True)
            hpause(6, 9)
            modal = handle_publish_modals(page, frame)
            print("发表后弹窗:", modal, flush=True)
        except Exception as e:
            print("发表JS失败:", e, flush=True)
        # 真实核实：发布成功会跳到 /platform/post/list（不再无条件报成功）
        # 定时发表同样跳 post/list（列表里状态显示"定时发布中"，可在助手里改期/取消）
        for _ in range(6):
            if "post/list" in page.url:
                break
            time.sleep(2)
        try:
            page.screenshot(path=os.path.join(SHOT_DIR, "_sph_published.png"), full_page=True, timeout=8000)
        except Exception:
            pass  # 截图失败不影响已完成的发布，也不能污染成功判定
        ok = "post/list" in page.url
        tail = f"（定时 {at}）" if at else ""
        print(("PUBLISH_OK ✓ 已" + ("设定定时发表" if at else "发布") + tail) if ok
              else "PUBLISH_FAIL ✗ 未跳转 post/list（看截图）", "| URL:", page.url, flush=True)
        time.sleep(2); ctx.close()


if __name__ == "__main__":
    main()
