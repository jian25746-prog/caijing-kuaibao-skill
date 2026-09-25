#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
youtube_upload.py — 按 发布物料.json 把某节目某天的 clips 上传到 YouTube（竖屏=Shorts，定时发布）。

⚠️ 本脚本尚未端到端实测（需先完成下方 OAuth 配置 + 一次真实跑通才可信赖）。

首次依赖:
  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
一次性 OAuth 配置:
  1) Google Cloud Console 建项目 → 启用 "YouTube Data API v3"
  2) 创建 OAuth 客户端ID(桌面应用) → 下载，存为
     节目上传/config/youtube_client_secret.json
  3) 首次运行弹浏览器授权，token 缓存到 节目上传/config/youtube_token.json
配额: 每次 videos.insert 约 1600 units，默认日配额 10000 → 约 6 条/天够用。

用法:
  python3 youtube_upload.py 7点财经快报 6月13日 --dry-run
  python3 youtube_upload.py 7点财经快报 6月13日 --publish-at 2026-06-13T07:00:00+08:00
不带 --publish-at → 上传为 private（你再自己定时/公开）。只读视频与物料、不写节目目录。
"""
import sys, os, json, argparse

UPLOAD_ROOT = os.environ.get("CAIJING_UPLOAD_HOME") or os.path.expanduser("~/caijing-distribute")  # 登录态/密钥/物料所在目录，不在仓库内
CONFIG_DIR = os.path.join(UPLOAD_ROOT, "config")
CLIENT_SECRET = os.path.join(CONFIG_DIR, "youtube_client_secret.json")
TOKEN = os.path.join(CONFIG_DIR, "youtube_token.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
CATEGORY_NEWS = "25"  # News & Politics


def get_service():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("✗ 缺依赖: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2")
        sys.exit(1)
    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET):
                print(f"✗ 缺 OAuth 客户端密钥: {CLIENT_SECRET}（见文件头配置步骤）")
                sys.exit(1)
            creds = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES).run_local_server(port=0)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_one(yt, clip, publish_at, dry, playlist_id=None, public=False):
    yc = clip.get("youtube", {})
    title = yc.get("title", "")[:100]
    if dry:
        print(f"  [dry] 第{clip['clip_num']}条 → {title}")
        return None
    from googleapiclient.http import MediaFileUpload
    body = {
        "snippet": {"title": title, "description": yc.get("description", ""),
                    "tags": yc.get("tags", []), "categoryId": CATEGORY_NEWS},
        "status": {"selfDeclaredMadeForKids": False,
                   "privacyStatus": ("public" if public else "private")},
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at  # RFC3339；privacyStatus 须 private
    media = MediaFileUpload(clip["video"], chunksize=-1, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    print(f"  ✓ 第{clip['clip_num']}条 上传完成 videoId={vid}")
    cov = clip.get("cover")
    if cov and os.path.exists(cov):
        try:
            from googleapiclient.http import MediaFileUpload as MFU
            yt.thumbnails().set(videoId=vid, media_body=MFU(cov)).execute()
        except Exception as e:
            print(f"    (缩略图失败，可手动: {e})")
    if playlist_id:
        try:
            yt.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
            print("    ✓ 已加入播放列表")
        except Exception as e:
            print(f"    (播放列表失败: {e})")
    fc = clip.get("pinned_comment")
    if fc:
        try:
            yt.commentThreads().insert(part="snippet", body={"snippet": {"videoId": vid,
                "topLevelComment": {"snippet": {"textOriginal": fc}}}}).execute()
            print("    ✓ 首评已发（置顶请到 Studio 手点）")
        except Exception as e:
            print(f"    (首评失败: {e})")
    return vid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("show")
    ap.add_argument("date")
    ap.add_argument("--publish-at", default=None, help="RFC3339，如 2026-06-13T07:00:00+08:00；不给则上传为 private")
    ap.add_argument("--only", type=int, default=None, help="只上传指定 clip_num（测试/补传单条用）")
    ap.add_argument("--public", action="store_true", help="直接公开（默认 private）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    show = a.show
    _shows = {k: v for k, v in json.load(open(os.path.join(CONFIG_DIR, "shows.json"), encoding="utf-8")).items() if not k.startswith("_")}
    if show not in _shows:  # 试别名（如 "7点财经快报" → 文件夹名 "7点财经播报"）
        for _k, _c in _shows.items():
            if show in _c.get("aliases", []):
                show = _k
                break
    mp = os.path.join(UPLOAD_ROOT, "物料", show, a.date, "发布物料.json")
    if not os.path.exists(mp):
        print(f"✗ 缺物料: {mp}（先跑 parse_products + 生成发布物料）")
        sys.exit(1)
    clips = json.load(open(mp, encoding="utf-8"))["clips"]
    if a.only:
        clips = [c for c in clips if c["clip_num"] == a.only]
        if not clips:
            print(f"✗ 物料里没有 clip_num={a.only}")
            sys.exit(1)
    print(f"YouTube 上传 {show} {a.date} · {len(clips)} 条" + ("（dry-run）" if a.dry_run else ""))
    playlist_id = _shows.get(show, {}).get("youtube_playlist_id")
    yt = None if a.dry_run else get_service()
    for c in clips:
        upload_one(yt, c, a.publish_at, a.dry_run, playlist_id, a.public)


if __name__ == "__main__":
    main()
