"""
renderer.py — 《7点财经快报》PNG切片生成器 v5
每条切片生成3类独立透明PNG（9:16，1440×2560）：
  hook_XX_<模板>.png              — HOOK四行，透明底
  bottom_XX_<模板>.png            — 研判卡，透明底
  narration_XX_p1_<模板>.png      — 旁白第1页，透明底（超长自动分页）

v5 变更：
  - 三图分离，透明底，在剪映中自由叠层
  - HOOK 两侧留白从 144px 收窄至 60px（更饱满）
  - HOOK 各行字号固定，溢出换行，不再压缩字号
  - NARRATION 读取 narration 字段全文，自动分页（每页≤5行）
  - strip_unsupported 自动将「…」替换为「...」防止渲染乱码

用法：
  python3 renderer.py <slices.json> <output_dir> [selected_indices]
  selected_indices 逗号分隔，如 "1,3,5"（不填则生成全部）
"""

import os, sys, json
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── 字体路径 ─────────────────────────────────────────────
LATIN_FP = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
CJK_FP   = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"

def find_fonts():
    for p in ["/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/System/Library/Fonts/STHeiti Medium.ttc",
              "/Library/Fonts/Arial Unicode MS.ttf"]:
        if os.path.exists(p): return p, p
    lat = LATIN_FP if os.path.exists(LATIN_FP) else CJK_FP
    cjk = CJK_FP   if os.path.exists(CJK_FP)   else LATIN_FP
    return lat, cjk

LATIN_PATH, CJK_PATH = find_fonts()

# ── 模板加载 ─────────────────────────────────────────────
def load_templates():
    config_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "config", "templates.json"))
    if not os.path.exists(config_path):
        print(f"⚠️  未找到 templates.json，使用内置默认模板")
        return _default_templates()
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}

def _default_templates():
    return {
        "T1_暗夜财经": {
            "line0_glow": (255,20,20), "line0_text": "#FFFFFF", "line0_stroke": "#CC0000",
            "line1_bg": "#FFD600", "line1_body": "#0A0A0A", "line1_tail": "#FF1A1A",
            "line2_strip": (15,15,20,215), "line2_accent": (255,26,26,255),
            "line2_grad_l": (255,255,136), "line2_grad_r": (136,204,255),
            "line3_text": "#FFE033", "line3_stroke": "#FF6060",
            "card_fill": (5,5,20,165), "card_border": "#FFD600",
            "good_color": "#00E676", "risk_color": "#FF4D4F", "strength_color": "#FADB14",
        }
    }

TEMPLATES     = load_templates()
DEFAULT_TNAME = "T1_暗夜财经"

def _norm(v):
    if isinstance(v, list): return tuple(v)
    return v

def get_template(name):
    # 1. 精确匹配
    if name in TEMPLATES:
        return {kk: _norm(vv) for kk, vv in TEMPLATES[name].items()}, name
    # 2. 前缀匹配：取 "T3" 前缀匹配 "T3_冷静分析"（JSON里写 "T3_分析" 也能命中）
    prefix = name.split('_')[0]
    for k in TEMPLATES:
        if k.startswith(prefix + '_'):
            return {kk: _norm(vv) for kk, vv in TEMPLATES[k].items()}, k
    # 3. 包含匹配（宽松回退）
    for k in TEMPLATES:
        if k.startswith(name) or name in k:
            return {kk: _norm(vv) for kk, vv in TEMPLATES[k].items()}, k
    # 4. 最终兜底
    return {kk: _norm(vv) for kk, vv in TEMPLATES[DEFAULT_TNAME].items()}, DEFAULT_TNAME

# ── 字体工具 ─────────────────────────────────────────────
def load_font(fp, size):
    return ImageFont.truetype(fp, size, index=0)

def is_cjk(ch):
    cp = ord(ch)
    # 注意：—(U+2014)、"" (U+201C/D)、''(U+2018/9) 已从此列表移除
    # 这些字符由 LiberationSans-Bold 渲染（该字体有对应字形），DroidSans 缺少这些字形会出现方块
    return (0x4E00<=cp<=0x9FFF or 0x3000<=cp<=0x303F or
            0xFF00<=cp<=0xFFEF or 0x3400<=cp<=0x4DBF or
            ch in '，。！？、：；「」『』【】《》（）～・')

def pick_font(ch, latin, cjk):
    return cjk if is_cjk(ch) else latin

def strip_unsupported(text):
    """清理不可渲染字符，并自动将全角省略号替换为英文省略号（防方块□）"""
    # 全角/中文弯引号 → 半角直引号（防止出现在 Python heredoc 中导致 SyntaxError）
    _QUOTE_MAP = {
        '“': '"', '”': '"',   # " "
        '‘': "'", '’': "'",   # ' '
        '「': '"', '」': '"',   # 「 」
        '『': '"', '』': '"',   # 『 』
        '＂': '"', '＇': "'",   # ＂ ＇（全角ASCII引号）
    }
    for src, dst in _QUOTE_MAP.items():
        text = text.replace(src, dst)
    text = text.replace('…', '...')
    result = []
    for ch in text:
        cp = ord(ch)
        if (0x4E00<=cp<=0x9FFF or 0x3000<=cp<=0x303F or 0xFF00<=cp<=0xFFEF or
                0x3400<=cp<=0x4DBF or 0x0020<=cp<=0x007E or 0x2500<=cp<=0x27FF or
                cp in (0x3002,0xff0c,0xff01,0xff1f,0x3001,0xff1a,0xff1b,
                       0x300a,0x300b,0x3010,0x3011,0xff08,0xff09,
                       0x2026,0x2014,0xff5e,0x30fb,0x201c,0x201d,0x2018,0x2019)):
            result.append(ch)
    return ''.join(result)


# ── 免责声明清洗（渲染防护层）──────────────────────────────────────────────
import re as _re

# 所有需要从 narration 图片中剔除的免责语变体
# 统一在渲染层过滤，无论 AI 是否生成，图面永不出现
_DISCLAIMER_PATTERNS = [
    r"[，,。]?\s*仅为个人观点[，,]?\s*不构成投资建议\s*[。]?",
    r"[，,。]?\s*不构成投资建议\s*[。]?",
    r"[，,。]?\s*仅为个人观点\s*[。]?",
    r"[，,。]?\s*本内容仅供参考[，,]?\s*不构成任何投资建议\s*[。]?",
    r"[，,。]?\s*投资有风险[，,]?\s*入市须谨慎\s*[。]?",
    r"[，,。]?\s*请独立判断风险\s*[。]?",
    # "但请注意，这是机构的逻辑，散户追仓需要独立判断风险" 类整句
    r"[。，]?但请注意[，,][^。]{0,30}独立判断风险\s*[，,。]?",
    # 独立出现的"散户追仓需要独立判断风险"（不带"但请注意"引导）
    r"[，,]?\s*散户[^。，]{0,10}独立判断风险\s*[，,。]?",
]

def strip_disclaimer(text: str) -> str:
    """
    从旁白文本中移除所有免责声明变体（渲染防护层）。
    片尾统一免责由脚本片尾处理，各切片 narration 无需重复声明。
    """
    for pat in _DISCLAIMER_PATTERNS:
        text = _re.sub(pat, "", text)
    # 清理因删除短语导致的多余标点/空格
    text = _re.sub(r"[。，]{2,}", "。", text)
    text = text.strip("，。 ")
    if text and text[-1] not in "。！？…":
        text += "。"
    return text

def process_strength_line(raw):
    if "极强"   in raw: return "【研判强度】极强  ●●●●●"
    if "较强"   in raw: return "【研判强度】较强  ●●●○○"
    if "震荡警戒" in raw: return "【研判强度】震荡警戒  ○○○○○"
    n = min(5, max(0, raw.count('\U0001f525')))
    return f'【研判强度】 {"●"*n}{"○"*(5-n)}'

def baseline_offset(latin, cjk):
    return max(0, cjk.getmetrics()[0] - latin.getmetrics()[0])

def mixed_width(draw, text, latin, cjk):
    w = 0
    for ch in text:
        b = draw.textbbox((0,0), ch, font=pick_font(ch,latin,cjk))
        w += b[2]-b[0]
    return w

def mixed_height(latin, cjk):
    la,ld = latin.getmetrics(); ca,cd = cjk.getmetrics()
    return max(la,ca)+max(ld,cd)

def draw_mixed(draw, pos, text, latin, cjk, fill,
               stroke_width=0, stroke_fill=None, bold=0):
    x, y = pos
    b_off = baseline_offset(latin, cjk)
    for ch in text:
        f = pick_font(ch, latin, cjk)
        y_ch = y if is_cjk(ch) else y+b_off
        kw = dict(font=f, fill=fill)
        if stroke_width and stroke_fill:
            kw["stroke_width"]=stroke_width; kw["stroke_fill"]=stroke_fill
        if bold > 0:
            for dx in range(-bold, bold+1):
                for dy in range(-bold, bold+1):
                    if dx==0 and dy==0: continue
                    draw.text((x+dx, y_ch+dy), ch, font=f, fill=fill)
        draw.text((x, y_ch), ch, **kw)
        b = draw.textbbox((0,0), ch, font=f); x += b[2]-b[0]
    return x

def _tokenize(text):
    """
    将文本拆分为「最小换行单元」列表：
    - CJK字符每字单独成一个 token（可在任意CJK字符后换行）
    - 连续非CJK字符（数字/英文/标点如 % . - 等）合并为一个不可分割 token
    这样可防止 "150美元" 被截为 "1" + "50美元" 等数字断行问题。
    """
    tokens, i = [], 0
    while i < len(text):
        if is_cjk(text[i]):
            tokens.append(text[i]); i += 1
        else:
            j = i
            while j < len(text) and not is_cjk(text[j]):
                j += 1
            tokens.append(text[i:j]); i = j
    return tokens

def wrap_to_width(draw, text, max_px, latin, cjk):
    """
    将文字按像素宽度折行，返回行列表。
    - CJK字符逐字换行；连续数字/英文作为整体不可分割
    - 含反孤行处理：末行≤3字时循环从前行末尾借字
    """
    def token_width(tok):
        return sum(draw.textbbox((0,0), ch, font=pick_font(ch,latin,cjk))[2] -
                   draw.textbbox((0,0), ch, font=pick_font(ch,latin,cjk))[0]
                   for ch in tok)

    lines, cur, cur_w = [], "", 0
    for tok in _tokenize(text):
        tw = token_width(tok)
        if cur_w + tw > max_px and cur:
            lines.append(cur); cur = tok; cur_w = tw
        else:
            cur += tok; cur_w += tw
    if cur: lines.append(cur)
    # 反孤行：末行字数≤3时从前行末尾借 CJK 字（只借CJK，不拆数字/英文序列）
    while (len(lines) >= 2 and len(lines[-1]) <= 3
           and len(lines[-2]) > 4 and is_cjk(lines[-2][-1])):
        lines[-1] = lines[-2][-1] + lines[-1]
        lines[-2] = lines[-2][:-1]
    return lines if lines else [""]

# ── 画布尺寸 & HOOK 边距 ──────────────────────────────────
W, H = 1440, 2560
HOOK_MARGIN = 60   # 两侧各留白60px（v4为144px），视觉更饱满

# ── HOOK 四行绘制（固定字号，溢出换行，不缩字号）────────────

def draw_hook_line0(img, text, y, t):
    """第0行：红色发光白字，固定108px，溢出自动换行"""
    FS = 108
    lat = load_font(LATIN_PATH, FS); cjk = load_font(CJK_PATH, FS)
    inner_w = W - HOOK_MARGIN * 2
    tmp = Image.new("RGBA",(10,10)); td = ImageDraw.Draw(tmp)
    lines = wrap_to_width(td, text, inner_w, lat, cjk)
    lh = mixed_height(lat, cjk); gc = t["line0_glow"]
    for line in lines:
        tw = mixed_width(ImageDraw.Draw(img), line, lat, cjk)
        x = (W-tw)//2
        for sw,alpha,blur in [(22,230,11),(11,200,3)]:
            gl = Image.new('RGBA', img.size, (0,0,0,0)); gd = ImageDraw.Draw(gl)
            draw_mixed(gd,(x,y),line,lat,cjk,(*gc,alpha),stroke_width=sw,stroke_fill=(*gc,alpha))
            img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(radius=blur)))
        draw_mixed(ImageDraw.Draw(img),(x,y),line,lat,cjk,
                   t["line0_text"],stroke_width=3,stroke_fill=t["line0_stroke"],bold=2)
        y += lh + 16
    return y + 8

def draw_hook_line1(img, text, y, t):
    """第1行：黄底色块，固定88px，溢出自动换行"""
    FS = 88
    lat = load_font(LATIN_PATH, FS); cjk = load_font(CJK_PATH, FS)
    inner_w = W - HOOK_MARGIN * 2
    tmp = Image.new("RGBA",(10,10)); td = ImageDraw.Draw(tmp)
    lines = wrap_to_width(td, text, inner_w, lat, cjk)
    lh = mixed_height(lat, cjk); pad_x, pad_y = 56, 18
    for line in lines:
        tw = mixed_width(ImageDraw.Draw(img), line, lat, cjk)
        x0 = (W-tw)//2
        bl=x0-pad_x; br=x0+tw+pad_x; bt=y+4; bb=y+lh+pad_y*2+4
        sh = Image.new('RGBA',(W,H),(0,0,0,0))
        ImageDraw.Draw(sh).rectangle([bl+5,bt+6,br+5,bb+6],fill=(0,0,0,90))
        img.alpha_composite(sh)
        bk = Image.new('RGBA',(W,H),(0,0,0,0))
        ImageDraw.Draw(bk).rectangle([bl,bt,br,bb],fill=t["line1_bg"])
        img.alpha_composite(bk)
        ty = y+pad_y+4
        body = line[:-5] if len(line)>5 else ""; tail = line[-5:] if len(line)>5 else line
        cx = x0; draw = ImageDraw.Draw(img)
        if body: cx = draw_mixed(draw,(cx,ty),body,lat,cjk,t["line1_body"],bold=2)
        draw_mixed(draw,(cx,ty),tail,lat,cjk,t["line1_tail"],bold=2)
        y = bb + 12
    return y + 4

def draw_hook_line2(img, text, y, t):
    """第2行：深色横条+渐变字，固定74px，溢出自动换行"""
    FS = 74
    lat = load_font(LATIN_PATH, FS); cjk = load_font(CJK_PATH, FS)
    inner_w = W - HOOK_MARGIN * 2
    tmp = Image.new("RGBA",(10,10)); td = ImageDraw.Draw(tmp)
    lines = wrap_to_width(td, text, inner_w, lat, cjk)
    lh = mixed_height(lat, cjk); pad_y = 18
    for line in lines:
        tw = mixed_width(ImageDraw.Draw(img), line, lat, cjk)
        xs = (W-tw)//2; sh_h = lh+pad_y*2
        strip = Image.new('RGBA',(W,sh_h), t["line2_strip"])
        img.alpha_composite(strip,(0,y))
        acc = Image.new('RGBA',(8,sh_h), t["line2_accent"])
        img.alpha_composite(acc,(0,y)); img.alpha_composite(acc,(W-8,y))
        draw = ImageDraw.Draw(img); ty = y+pad_y
        b_off = baseline_offset(lat,cjk)
        cl,cr = t["line2_grad_l"],t["line2_grad_r"]; cx = xs
        for ch in line:
            f=pick_font(ch,lat,cjk); y_ch=ty if is_cjk(ch) else ty+b_off
            cb=draw.textbbox((0,0),ch,font=f); cw=cb[2]-cb[0]
            p=max(0.0,min(1.0,(cx-xs)/max(tw,1)))
            r=int(cl[0]+(cr[0]-cl[0])*p); g=int(cl[1]+(cr[1]-cl[1])*p); b=int(cl[2]+(cr[2]-cl[2])*p)
            for dx in range(-2,3):
                for dy in range(-2,3):
                    if dx==0 and dy==0: continue
                    draw.text((cx+dx,y_ch+dy),ch,font=f,fill=(r,g,b,255))
            draw.text((cx,y_ch),ch,font=f,fill=(r,g,b,255)); cx+=cw
        y += sh_h + 10
    return y + 4

def draw_hook_line3(img, text, y, t):
    """第3行：橙黄描边字，固定68px，溢出自动换行"""
    FS = 68
    lat = load_font(LATIN_PATH, FS); cjk = load_font(CJK_PATH, FS)
    inner_w = W - HOOK_MARGIN * 2
    tmp = Image.new("RGBA",(10,10)); td = ImageDraw.Draw(tmp)
    lines = wrap_to_width(td, text, inner_w, lat, cjk)
    lh = mixed_height(lat, cjk); draw = ImageDraw.Draw(img)
    for line in lines:
        tw = mixed_width(draw, line, lat, cjk); x=(W-tw)//2
        draw_mixed(draw,(x+3,y+5),line,lat,cjk,(0,0,0,130))
        draw_mixed(draw,(x,y),line,lat,cjk,
                   t["line3_text"],stroke_width=4,stroke_fill=t["line3_stroke"],bold=2)
        y += lh+16
    return y+4

# ── 水印 ─────────────────────────────────────────────────
def add_watermark(img):
    lat=load_font(LATIN_PATH,44); cjk=load_font(CJK_PATH,44)
    draw=ImageDraw.Draw(img)
    text=os.environ.get("CAIJING_WATERMARK", "凡人投资 | 仅供参考，不构成投资建议")
    tw=mixed_width(draw,text,lat,cjk); x=(W-tw)//2; y=H-110
    draw_mixed(draw,(x+2,y+2),text,lat,cjk,(0,0,0,100))
    draw_mixed(draw,(x,y),    text,lat,cjk,(220,220,220,180))

# ── 生成 HOOK PNG ─────────────────────────────────────────
# ── 时效徽章（顶部右上角胶囊） ───────────────────────────────
_URGENCY_COLORS = {
    'breaking': '#E63946',   # 红色 - 突发（<1h）
    'today':    '#FFB300',   # 琥珀 - 今日（<24h）
    'archive':  '#9E9E9E',   # 灰色 - 回顾（>24h）
}
_URGENCY_LABELS = {
    'breaking': '突发',
    'today':    '今日',
    'archive':  '回顾',
}

def draw_timestamp_band(img, t, time_text, batch_label, urgency):
    """
    在 hook 顶部右上角绘制时效徽章 + 批次胶囊。
    time_text:   "5月18日 20:00"
    batch_label: "晨报"/"午报"/"盘后"/"晚间"/"突发"
    urgency:     "breaking"/"today"/"archive"
    """
    if not time_text and not batch_label:
        return
    draw = ImageDraw.Draw(img)

    dot_color = _URGENCY_COLORS.get(urgency, '#FFB300')
    u_label   = _URGENCY_LABELS.get(urgency, '今日')

    FS = 40
    lat = load_font(LATIN_PATH, FS)
    cjk = load_font(CJK_PATH,   FS)

    left_text  = f"● {u_label}"
    right_text = f"{batch_label} · {time_text}" if batch_label else time_text
    gap_text   = "   "

    full_text  = left_text + gap_text + right_text
    text_w = mixed_width(draw, full_text, lat, cjk)
    pad_x, pad_y = 32, 16
    pill_w = text_w + pad_x * 2
    line_h = mixed_height(lat, cjk)
    pill_h = line_h + pad_y * 2

    margin_top  = 90
    margin_right = 80
    x = W - pill_w - margin_right
    y = margin_top

    # 阴影
    shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x+6, y+6, x+pill_w+6, y+pill_h+6],
                          radius=pill_h//2, fill=(0, 0, 0, 110))
    img.alpha_composite(shadow)

    # 胶囊主体（半透明深底 + 时效色描边）
    pill = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill)
    pd.rounded_rectangle([x, y, x+pill_w, y+pill_h],
                          radius=pill_h//2,
                          fill=(15, 15, 22, 235),
                          outline=dot_color, width=5)
    img.alpha_composite(pill)

    # 绘制文字
    tx = x + pad_x
    ty = y + pad_y - 4

    # 左：彩点 + 紧急标签
    draw_mixed(draw, (tx, ty), left_text, lat, cjk,
               dot_color, stroke_width=2, stroke_fill="#000000")

    left_w = mixed_width(draw, left_text + gap_text, lat, cjk)

    # 右：批次 · 时间（白字）
    draw_mixed(draw, (tx + left_w, ty), right_text, lat, cjk,
               "#FFFFFF", stroke_width=2, stroke_fill="#000000")


def draw_hook_title(img, text, y, t):
    """
    Hook 顶部品牌标题行：如"5月18日财经快报"，居中，与hook字体同等级，
    用模板 line3 配色（accent 色 + 描边），简洁不抢戏。
    """
    FS = 88   # 与 hook 第二行同大小，最显眼且不压过 line0
    lat = load_font(LATIN_PATH, FS)
    cjk = load_font(CJK_PATH,   FS)
    inner_w = W - HOOK_MARGIN * 2
    tmp = Image.new("RGBA",(10,10)); td = ImageDraw.Draw(tmp)
    lines = wrap_to_width(td, text, inner_w, lat, cjk)
    lh = mixed_height(lat, cjk)
    draw = ImageDraw.Draw(img)
    for line in lines:
        tw = mixed_width(draw, line, lat, cjk)
        x = (W - tw) // 2
        # 阴影
        draw_mixed(draw, (x+4, y+6), line, lat, cjk, (0,0,0,140))
        # 主体（accent 色 + 描边）
        draw_mixed(draw, (x, y), line, lat, cjk,
                   t["line3_text"], stroke_width=4,
                   stroke_fill=t["line3_stroke"], bold=2)
        y += lh + 18
    return y + 16


def render_hook(index, hook_lines, template_name, output_dir, meta=None):
    if not isinstance(hook_lines, list) or len(hook_lines) != 4:
        raise ValueError(f"第{index:02d}条 hook 必须恰好4行，实际为 {len(hook_lines) if isinstance(hook_lines, list) else '非数组'}")
    t, tname = get_template(template_name)
    img = Image.new('RGBA',(W,H),(0,0,0,0))

    # 顶部品牌标题（meta.title_banner 提供则渲染）
    cy = 270
    if meta:
        title_banner = meta.get("title_banner", "")
        if title_banner:
            # 标题起点放在 y=130，结束后下一条 hook 自然衔接
            title_end_y = draw_hook_title(img, title_banner, 130, t)
            # 若标题占用空间已超过默认 270，则将 hook 起点下推
            if title_end_y + 20 > cy:
                cy = title_end_y + 20

    funcs = [draw_hook_line0, draw_hook_line1, draw_hook_line2, draw_hook_line3]
    for i, raw in enumerate(hook_lines):
        text = strip_unsupported(raw)
        if not text: continue
        cy = funcs[i](img, text, cy, t)
    add_watermark(img)
    out = os.path.join(output_dir, f"hook_{index:02d}_{tname}.png")
    img.save(out, optimize=True)
    print(f"  🎣 hook_{index:02d}   ({os.path.getsize(out)//1024} KB)")
    return out

# ── 生成 BOTTOM PNG ───────────────────────────────────────
def render_bottom(index, bottom_lines, template_name, output_dir):
    t, tname = get_template(template_name)
    img = Image.new('RGBA',(W,H),(0,0,0,0))
    FONT_SIZE=50; PAD_X,PAD_Y=50,44; CARD_W=W-100
    INNER_W=CARD_W-PAD_X*2; LINE_GAP=22
    lat=load_font(LATIN_PATH,FONT_SIZE); cjk=load_font(CJK_PATH,FONT_SIZE)
    rendered=[]
    for raw in bottom_lines:
        if "研判强度" in raw:
            cleaned=strip_unsupported(process_strength_line(raw)); color=t["strength_color"]
        else:
            cleaned=strip_unsupported(raw); color="#FFFFFF"
            if "核心利好" in raw:
                rest=raw.split("：",1)[-1] if "：" in raw else raw
                cleaned=strip_unsupported("【核心利好】 "+rest); color=t["good_color"]
            elif "风险警报" in raw:
                rest=raw.split("：",1)[-1] if "：" in raw else raw
                cleaned=strip_unsupported("【风险警报】 "+rest); color=t["risk_color"]
        tmp=Image.new("RGBA",(10,10)); td=ImageDraw.Draw(tmp)
        rendered.append((wrap_to_width(td,cleaned,INNER_W,lat,cjk),color))
    draw=ImageDraw.Draw(img); line_h=mixed_height(lat,cjk)
    total_h=PAD_Y*2
    for (wl,_) in rendered: total_h+=len(wl)*(line_h+LINE_GAP)
    total_h-=LINE_GAP
    bx=(W-CARD_W)//2; by=H-total_h-180
    sh=Image.new('RGBA',(W,H),(0,0,0,0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [bx+8,by+8,bx+CARD_W+8,by+total_h+8],radius=28,fill=(0,0,0,80))
    img.alpha_composite(sh)
    card=Image.new('RGBA',(W,H),(0,0,0,0)); cd=ImageDraw.Draw(card)
    cd.rounded_rectangle([bx,by,bx+CARD_W,by+total_h],radius=28,fill=t["card_fill"])
    cd.rounded_rectangle([bx,by,bx+CARD_W,by+total_h],radius=28,outline=t["card_border"],width=6)
    img.alpha_composite(card)
    draw=ImageDraw.Draw(img); cy=by+PAD_Y
    for (wlines,color) in rendered:
        for wl in wlines:
            draw_mixed(draw,(bx+PAD_X+2,cy+3),wl,lat,cjk,(0,0,0,120))
            draw_mixed(draw,(bx+PAD_X,cy),wl,lat,cjk,color,stroke_width=2,stroke_fill="#050514")
            cy+=line_h+LINE_GAP
    add_watermark(img)
    out = os.path.join(output_dir, f"bottom_{index:02d}_{tname}.png")
    img.save(out, optimize=True)
    print(f"  📊 bottom_{index:02d}  ({os.path.getsize(out)//1024} KB)")
    return out

# ── 生成 NARRATION PNG（全文自动分页）────────────────────────
def render_narration(index, narration_text, template_name, output_dir):
    """
    旁白全文渲染，字号60px固定不压缩，超出5行自动分第2页。
    每页约承载80字，输出 narration_XX_p1.png / p2.png ...
    """
    t, tname = get_template(template_name)
    FONT_SIZE       = 60
    MARGIN_X        = 144
    PAD_X, PAD_Y    = 60, 50
    LINE_GAP        = 22
    MAX_LINES_PER_PAGE = 5   # 每页≤5行（约80字），保持与原版相同承载量

    lat = load_font(LATIN_PATH, FONT_SIZE)
    cjk = load_font(CJK_PATH,   FONT_SIZE)

    text    = strip_disclaimer(strip_unsupported(narration_text))
    inner_w = W - MARGIN_X*2 - PAD_X*2
    box_w   = W - MARGIN_X*2
    box_x   = MARGIN_X

    tmp = Image.new("RGBA",(10,10)); td = ImageDraw.Draw(tmp)
    all_lines = wrap_to_width(td, text, inner_w, lat, cjk)
    pages = [all_lines[i:i+MAX_LINES_PER_PAGE]
             for i in range(0, max(1,len(all_lines)), MAX_LINES_PER_PAGE)]

    line_h    = mixed_height(lat, cjk)
    out_files = []

    for page_num, page_lines in enumerate(pages, 1):
        img = Image.new('RGBA',(W,H),(0,0,0,0))
        total_text_h = len(page_lines)*(line_h+LINE_GAP) - LINE_GAP
        total_box_h  = total_text_h + PAD_Y*2
        box_y = H - total_box_h - 180

        overlay = Image.new('RGBA',(W,H),(0,0,0,0)); od = ImageDraw.Draw(overlay)
        od.rounded_rectangle([box_x,box_y,box_x+box_w,box_y+total_box_h],
                             radius=28, fill=(0,0,0,170))
        od.rounded_rectangle([box_x,box_y,box_x+box_w,box_y+total_box_h],
                             radius=28, outline=t["card_border"], width=4)
        img.alpha_composite(overlay)

        draw = ImageDraw.Draw(img); cy = box_y+PAD_Y
        for line in page_lines:
            draw_mixed(draw,(box_x+PAD_X+2,cy+3),line,lat,cjk,(0,0,0,120))
            draw_mixed(draw,(box_x+PAD_X,cy),line,lat,cjk,
                       "#FFFFFF",stroke_width=2,stroke_fill="#050514")
            cy += line_h+LINE_GAP

        add_watermark(img)
        suffix = f"p{page_num}" if len(pages)>1 else "p1"
        out = os.path.join(output_dir, f"narration_{index:02d}_{suffix}_{tname}.png")
        img.save(out, optimize=True)
        out_files.append(out)
        print(f"  📝 narration_{index:02d}_{suffix}  ({os.path.getsize(out)//1024} KB)")

    return out_files

# ── 主生成函数 ────────────────────────────────────────────
def generate(index, hook_lines, bottom_lines, narration, template_name, output_dir, meta=None):
    """每条切片输出3类独立透明PNG：hook / bottom / narration（自动分页）。"""
    outputs = [render_hook(index, hook_lines, template_name, output_dir, meta=meta)]
    outputs.append(render_bottom(index, bottom_lines, template_name, output_dir))
    outputs.extend(render_narration(index, narration, template_name, output_dir))
    return outputs


def clear_index_outputs(output_dir, index):
    """仅清理本次将重渲染序号的旧PNG，避免重跑残留页混入成品。"""
    import glob
    removed = 0
    for prefix in ("hook", "bottom", "narration"):
        pattern = os.path.join(output_dir, f"{prefix}_{index:02d}_*.png")
        for path in glob.glob(pattern):
            os.remove(path)
            removed += 1
    if removed:
        print(f"  🧹 清理第{index:02d}条旧PNG：{removed}张")


def validate_rendered_outputs(index, outputs):
    """确认每条都有hook、bottom和至少一页narration，且文件非空。"""
    names = [os.path.basename(path) for path in outputs]
    required = {
        "hook": any(name.startswith(f"hook_{index:02d}_") for name in names),
        "bottom": any(name.startswith(f"bottom_{index:02d}_") for name in names),
        "narration": any(name.startswith(f"narration_{index:02d}_") for name in names),
    }
    missing = [kind for kind, ok in required.items() if not ok]
    empty = [path for path in outputs if not os.path.exists(path) or os.path.getsize(path) == 0]
    if missing or empty:
        raise RuntimeError(f"第{index:02d}条渲染不完整：缺少={missing}，空文件={empty}")

# ── 入口 ─────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python3 renderer.py <slices.json> <output_dir> [1,3,5]")
        sys.exit(1)

    json_path  = sys.argv[1]
    output_dir = sys.argv[2]
    selected   = ([int(x) for x in sys.argv[3].split(",")]
                  if len(sys.argv) > 3 else None)

    os.makedirs(output_dir, exist_ok=True)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list) or not data:
        raise ValueError("slices.json 顶层必须是非空数组")
    if selected is not None:
        invalid = sorted({i for i in selected if i < 1 or i > len(data)})
        if invalid:
            raise ValueError(f"选中序号超出范围：{invalid}")

    count = sum(1 for i,_ in enumerate(data,1)
                if selected is None or i in selected)
    print(f"🎨 开始生成PNG切片 v5-三分离透明图（共{count}条切片）")
    cfg = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "../config/templates.json"))
    print(f"📋 模板配置：{cfg}\n")

    for i, item in enumerate(data, 1):
        if selected is None or i in selected:
            required_fields = ("news_id", "序号", "title", "hook", "bottom", "narration", "title_banner")
            missing = [field for field in required_fields if field not in item or item.get(field) in (None, "", [])]
            if missing:
                raise ValueError(f"第{i:02d}条缺少必填字段：{missing}")
            tpl       = item.get("template",  DEFAULT_TNAME)
            narration = item.get("narration", "")
            title_preview = item.get("title","")[:18]
            # 时效元数据（可选）：title_banner = "5月18日财经播报"
            meta = {
                "title_banner":         item.get("title_banner", ""),
            }
            print(f"[{i:02d}] {title_preview}...")
            clear_index_outputs(output_dir, i)
            outputs = generate(i, item["hook"], item["bottom"], narration, tpl, output_dir, meta=meta)
            validate_rendered_outputs(i, outputs)
            print()

    print("🎉 全部完成！")
