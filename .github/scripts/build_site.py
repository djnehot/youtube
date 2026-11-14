"""
DJ Ne-Hot — YouTube Auto Page Updater v3
- Fetches ALL videos + shorts (full pagination, no limit)
- Latest video plays INSIDE the spinning vinyl
- Fancy cinematic design
- Pushes index.html to GitHub Pages
- API key never leaves your PC
"""

import os, re, subprocess, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────
#  CONFIGURATION — runs inside GitHub Actions.
#  The API key comes from the repo secret YOUTUBE_API_KEY (never committed).
# ─────────────────────────────────────────────
YOUTUBE_API_KEY   = os.environ.get("YOUTUBE_API_KEY", "")
CHANNEL_ID        = os.environ.get("CHANNEL_ID", "UCSEZH_CUNJgjVHJb0jRUvcQ")
REPO_ROOT         = Path(__file__).resolve().parents[2]
OUTPUT_FILE       = REPO_ROOT / "index.html"
STATE_FILE        = REPO_ROOT / ".github" / "last_video_id.txt"
FORCE             = os.environ.get("FORCE", "").lower() == "true"
TZ                = ZoneInfo("Europe/Istanbul")
# ─────────────────────────────────────────────

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests


def fetch_latest_video_id(api_key, playlist_id):
    """Cheap check (1 quota unit): newest upload's ID."""
    r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems", params={
        "part": "contentDetails", "playlistId": playlist_id,
        "maxResults": 1, "key": api_key
    })
    r.raise_for_status()
    items = r.json().get("items", [])
    return items[0]["contentDetails"]["videoId"] if items else ""


def fetch_channel_info(api_key, channel_id):
    r = requests.get("https://www.googleapis.com/youtube/v3/channels", params={
        "part": "contentDetails,snippet,statistics",
        "id": channel_id, "key": api_key
    })
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise ValueError("Channel not found. Check CHANNEL_ID.")
    item = items[0]
    playlist = item["contentDetails"]["relatedPlaylists"]["uploads"]
    title    = item["snippet"]["title"]
    subs     = int(item["statistics"].get("subscriberCount", 0))
    return playlist, title, subs


def fetch_all_videos(api_key, playlist_id):
    """Fetch EVERY video — handles all pages."""
    videos, url = [], "https://www.googleapis.com/youtube/v3/playlistItems"
    params = {
        "part": "snippet,contentDetails",
        "playlistId": playlist_id,
        "maxResults": 50,
        "key": api_key
    }
    page = 0
    while True:
        page += 1
        print(f"   Fetching page {page}...")
        r = requests.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            sn     = item["snippet"]
            vid_id = sn["resourceId"]["videoId"]
            # skip deleted/private videos
            if vid_id == "deleted video" or sn.get("title") == "Private video":
                continue
            thumbs = sn.get("thumbnails", {})
            thumb  = (thumbs.get("maxres") or thumbs.get("high") or
                      thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            if not thumb:
                thumb = f"https://img.youtube.com/vi/{vid_id}/mqdefault.jpg"
            videos.append({
                "id":          vid_id,
                "title":       sn.get("title", ""),
                "published":   sn.get("publishedAt", ""),
                "thumbnail":   thumb,
            })
        npt = data.get("nextPageToken")
        if not npt:
            break
        params["pageToken"] = npt
    return videos


def enrich_videos(api_key, videos):
    """Add duration (for Shorts detection) + view/like counts in batches of 50."""
    enriched = {}
    ids = [v["id"] for v in videos]
    for i in range(0, len(ids), 50):
        batch = ids[i:i+50]
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "contentDetails,statistics",
            "id":   ",".join(batch),
            "key":  api_key
        })
        r.raise_for_status()
        for item in r.json().get("items", []):
            dur   = item["contentDetails"]["duration"]
            stats = item.get("statistics", {})
            enriched[item["id"]] = {
                "is_short": _is_short(dur),
                "views":    int(stats.get("viewCount",  0)),
                "likes":    int(stats.get("likeCount",  0)),
            }
    for v in videos:
        v.update(enriched.get(v["id"], {"is_short": False, "views": 0, "likes": 0}))
    return videos


def _is_short(iso):
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso)
    if not m:
        return False
    total = int(m.group(1) or 0)*3600 + int(m.group(2) or 0)*60 + int(m.group(3) or 0)
    return total <= 61   # 61s to catch borderline shorts


def fmt(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)


def card(v):
    vid  = v["id"]
    t    = v["title"].replace('"','&quot;').replace("'","&#39;")
    is_s = v.get("is_short", False)
    fn   = f"playVideo('{vid}',{str(is_s).lower()})"
    th   = v.get("thumbnail", f"https://img.youtube.com/vi/{vid}/mqdefault.jpg")
    bdg  = '<span class="bs">🩳</span>' if is_s else '<span class="bv">▶</span>'
    vw   = fmt(v.get("views",0))
    lk   = fmt(v.get("likes",0))
    return (
        f'<a class="vc" onclick="{fn}" href="javascript:void(0)" title="{t}">'
        f'<div class="tw"><img src="{th}" alt="{t}" loading="lazy" '
        f'onerror="this.src=\'https://img.youtube.com/vi/{vid}/mqdefault.jpg\'">'
        f'<div class="po"><div class="pc"><svg width="14" height="14" viewBox="0 0 14 14">'
        f'<polygon points="3,1 13,7 3,13" fill="white"/></svg></div></div></div>'
        f'<div class="vi"><div class="vt">{v["title"]}</div>'
        f'<div class="vm">{bdg}<span>👁 {vw}</span><span>❤️ {lk}</span></div></div></a>'
    )


def generate_html(videos, subs):
    shorts  = [v for v in videos if v.get("is_short")]
    regular = [v for v in videos if not v.get("is_short")]
    latest  = videos[0] if videos else None
    latest_id    = latest["id"]    if latest else "vBJ_UVS9OX8"
    latest_title = latest["title"] if latest else "DJ Ne-Hot"
    updated = datetime.now(TZ).strftime("%d %b %Y · %H:%M")

    reg_cards = "\n".join(card(v) for v in regular)
    sho_cards = "\n".join(card(v) for v in shorts)

    shorts_block = ""
    if shorts:
        shorts_block = f"""
<div class="sdiv"><span>🩳 Shorts ({len(shorts)})</span></div>
<div class="grid sg" id="shortsGrid">
{sho_cards}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>DJ Ne-Hot — Official</title>
<meta property="og:title" content="DJ Ne-Hot — Official">
<meta property="og:description" content="Tüm müzik videoları. İzle, beğen, yorum yap.">
<meta name="theme-color" content="#000">
<link href="https://fonts.googleapis.com/css2?family=Anton&family=DM+Serif+Display:ital@0;1&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
:root{{--w:#f0ece4;--b:#000;--g:#111;--gold:#c8a84b;--red:#e02200}}
html{{scroll-behavior:smooth}}
body{{background:var(--b);color:var(--w);font-family:'Space Mono',monospace;overflow-x:hidden;cursor:none}}

/* cursor */
#cur{{width:12px;height:12px;border:1.5px solid var(--w);border-radius:50%;position:fixed;pointer-events:none;z-index:9999;transform:translate(-50%,-50%);transition:transform .12s,background .12s,width .12s,height .12s}}
#cur.h{{background:var(--w);width:22px;height:22px}}

/* canvas bg */
#bg{{position:fixed;inset:0;z-index:0;opacity:.3}}

/* noise */
.noise{{position:fixed;inset:-50%;width:200%;height:200%;opacity:.05;z-index:1;pointer-events:none;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 512 512' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  animation:ns .5s steps(2) infinite}}
@keyframes ns{{0%{{transform:translate(0,0)}}25%{{transform:translate(-1%,1%)}}75%{{transform:translate(1%,-1%)}}}}

/* scanlines */
.sl{{position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.07) 2px,rgba(0,0,0,.07) 4px);z-index:2;pointer-events:none}}

/* HERO */
.hero{{position:relative;z-index:10;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 20px 50px;gap:0}}

/* ── VINYL STAGE ── */
.vstage{{position:relative;width:min(340px,80vw);height:min(340px,80vw);margin-bottom:40px;flex-shrink:0}}

/* outer glow ring */
.vglow{{position:absolute;inset:-28px;border-radius:50%;
  background:conic-gradient(from 0deg,rgba(200,168,75,0.15),rgba(200,168,75,0.05),rgba(200,168,75,0.18),rgba(200,168,75,0.04),rgba(200,168,75,0.15));
  animation:vgspin 8s linear infinite;filter:blur(12px)}}
@keyframes vgspin{{to{{transform:rotate(360deg)}}}}

/* vinyl disk */
.vdisk{{position:absolute;inset:0;border-radius:50%;overflow:hidden;
  background:radial-gradient(circle,#1c1c1c 0%,#0a0a0a 55%,#000 100%);
  box-shadow:0 0 0 1px #1f1f1f,0 0 50px rgba(0,0,0,.9),0 20px 60px rgba(0,0,0,.8);
  animation:vspin 5s linear infinite}}
.vdisk.paused{{animation-play-state:paused}}
@keyframes vspin{{to{{transform:rotate(360deg)}}}}

/* grooves */
.vdisk::before{{content:'';position:absolute;inset:0;border-radius:50%;
  background:repeating-radial-gradient(circle at 50%,transparent 0,transparent 4px,rgba(255,255,255,.014) 4px,rgba(255,255,255,.014) 5px)}}

/* shine */
.vdisk::after{{content:'';position:absolute;top:6%;left:12%;width:38%;height:28%;
  background:radial-gradient(ellipse,rgba(255,255,255,.07) 0%,transparent 70%);
  border-radius:50%;transform:rotate(-25deg)}}

/* iframe mask — circular clip inside the label area */
.vlabel{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:38%;height:38%;border-radius:50%;overflow:hidden;z-index:6;
  border:2px solid #2a2a2a;box-shadow:0 0 20px rgba(0,0,0,.8);
  background:#000;animation:vspin-rev 5s linear infinite}}
.vlabel.paused{{animation-play-state:paused}}
@keyframes vspin-rev{{to{{transform:translate(-50%,-50%) rotate(-360deg)}}}}

/* iframe inside label — counter-rotate to stay upright */
.vlabel-inner{{width:100%;height:100%;animation:vspin 5s linear infinite;overflow:hidden;border-radius:50%}}
.vlabel-inner.paused{{animation-play-state:paused}}

#vinylThumb{{width:100%;height:100%;object-fit:cover;border-radius:50%;display:block}}
#vinylFrame{{position:absolute;inset:0;width:100%;height:100%;border:none;border-radius:50%;display:none;pointer-events:none}}

/* hole */
.vhole{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:7px;height:7px;background:#000;border-radius:50%;border:1px solid #333;z-index:8}}

/* tonearm */
.tarm{{position:absolute;top:4%;right:-4%;width:46%;height:2px;
  background:linear-gradient(90deg,transparent,#777,#bbb,#777);
  transform-origin:right center;transform:rotate(-38deg);z-index:20;border-radius:2px;
  transition:transform 1.2s cubic-bezier(.4,0,.2,1)}}
.tarm::before{{content:'';position:absolute;right:-7px;top:-6px;width:14px;height:14px;
  background:radial-gradient(circle,#ccc,#555);border-radius:50%}}
.tarm::after{{content:'';position:absolute;left:0;top:-2px;width:7px;height:6px;background:var(--gold);border-radius:1px}}
.tarm.on{{transform:rotate(-17deg)}}

/* ── TITLE ── */
.tblock{{text-align:center;z-index:10}}
.eye{{font-size:10px;letter-spacing:7px;color:var(--gold);text-transform:uppercase;margin-bottom:14px;opacity:0;animation:fu 1s .3s forwards}}
.mt{{font-family:'Anton',sans-serif;font-size:clamp(60px,17vw,130px);letter-spacing:-2px;line-height:.85;
  background:linear-gradient(180deg,#fff 0%,#d8d4cc 35%,#888 72%,#2e2e2e 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  filter:drop-shadow(0 3px 0 rgba(0,0,0,.9));
  opacity:0;animation:fu 1s .5s forwards;position:relative}}
/* glitch layers */
.mt::before,.mt::after{{content:attr(data-text);position:absolute;inset:0;
  background:inherit;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}}
.mt::before{{clip-path:polygon(0 28%,100% 28%,100% 46%,0 46%);animation:g1 7s infinite}}
.mt::after{{clip-path:polygon(0 62%,100% 62%,100% 78%,0 78%);animation:g2 7s infinite}}
@keyframes g1{{0%,93%,100%{{transform:translate(0);opacity:0}}94%{{transform:translate(-4px,1px);opacity:.7;filter:hue-rotate(80deg)}}95%{{transform:translate(3px,-1px);opacity:.4}}}}
@keyframes g2{{0%,90%,100%{{transform:translate(0);opacity:0}}91%{{transform:translate(4px,2px);opacity:.6;filter:hue-rotate(-80deg)}}92%{{transform:translate(-2px,-2px);opacity:.3}}}}
.sl2{{font-family:'DM Serif Display',serif;font-style:italic;font-size:clamp(15px,3vw,24px);color:#666;letter-spacing:3px;margin-top:10px;opacity:0;animation:fu 1s .7s forwards}}
.dline{{width:70px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);margin:18px auto;opacity:0;animation:fu 1s .9s forwards}}
@keyframes fu{{from{{opacity:0;transform:translateY(18px)}}to{{opacity:1;transform:translateY(0)}}}}

/* latest title under vinyl */
.latest-label{{font-size:10px;letter-spacing:3px;color:#444;text-transform:uppercase;text-align:center;margin-bottom:32px;max-width:300px;opacity:0;animation:fu 1s 1.4s forwards}}
.latest-label span{{color:#888}}

/* play btn */
.pbtn{{display:inline-flex;align-items:center;gap:10px;padding:14px 30px;
  background:var(--w);color:var(--b);font-family:'Space Mono',monospace;
  font-size:11px;letter-spacing:3px;text-transform:uppercase;
  border:none;cursor:none;position:relative;overflow:hidden;
  box-shadow:4px 4px 0 var(--gold);transition:transform .2s,box-shadow .2s;
  opacity:0;animation:fu 1s 1.1s forwards}}
.pbtn::after{{content:'';position:absolute;inset:0;
  background:linear-gradient(120deg,transparent 30%,rgba(255,255,255,.15) 50%,transparent 70%);
  transform:translateX(-100%);transition:transform .4s}}
.pbtn:hover::after{{transform:translateX(100%)}}
.pbtn:hover{{transform:translateY(-2px);box-shadow:6px 6px 0 var(--gold)}}
.pbtn2{{background:transparent;color:var(--w);border:1px solid #333;box-shadow:4px 4px 0 #222;margin-left:10px}}
.pbtn2:hover{{border-color:#666;box-shadow:6px 6px 0 #333}}
.bwrap{{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-top:4px}}

/* scroll hint */
.shint{{position:absolute;bottom:28px;left:50%;transform:translateX(-50%);
  font-size:9px;letter-spacing:4px;color:#2a2a2a;text-transform:uppercase;
  display:flex;flex-direction:column;align-items:center;gap:8px;
  opacity:0;animation:fu 1s 1.6s forwards}}
.sarr{{width:1px;height:36px;background:linear-gradient(180deg,#333,transparent);animation:sp 1.6s ease-in-out infinite}}
@keyframes sp{{0%,100%{{opacity:.3}}50%{{opacity:1}}}}

/* stats strip */
.strip{{position:relative;z-index:10;display:flex;justify-content:center;gap:32px;
  padding:20px 24px;border-top:1px solid #111;border-bottom:1px solid #111;
  font-size:10px;letter-spacing:2px;color:#444;text-transform:uppercase;margin-bottom:0}}
.strip strong{{color:var(--gold);font-size:13px;display:block;margin-bottom:2px}}

/* videos section */
.vsec{{position:relative;z-index:10;padding:40px 16px 100px;max-width:1200px;margin:0 auto}}
.shead{{display:flex;align-items:baseline;gap:12px;margin-bottom:24px;padding-bottom:14px;border-bottom:1px solid #161616}}
.stitle{{font-family:'Anton',sans-serif;font-size:clamp(26px,5vw,44px);letter-spacing:3px;
  background:linear-gradient(90deg,var(--w),#666);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.scnt{{font-size:10px;letter-spacing:2px;color:var(--gold);text-transform:uppercase;margin-left:auto}}
.utag{{font-size:10px;letter-spacing:2px;color:#222;text-transform:uppercase;text-align:center;margin-bottom:24px}}

/* search */
.sw{{position:relative;margin-bottom:28px}}
.sw input{{width:100%;background:var(--g);border:none;border-bottom:1px solid #1a1a1a;
  color:var(--w);font-family:'Space Mono',monospace;font-size:12px;letter-spacing:1px;
  padding:13px 16px 13px 42px;outline:none;transition:border-color .2s;cursor:none}}
.sw input:focus{{border-bottom-color:var(--gold)}}
.sw input::placeholder{{color:#2a2a2a}}
.si{{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:#333;font-size:15px;pointer-events:none}}

/* grid */
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:2px}}
.sg{{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}}

/* video card */
.vc{{background:var(--g);cursor:none;display:block;text-decoration:none;color:inherit;
  overflow:hidden;transition:transform .3s cubic-bezier(.22,1,.36,1);
  opacity:0;transform:translateY(20px)}}
.vc:hover{{transform:scale(1.025);z-index:5}}
.tw{{position:relative;aspect-ratio:16/9;overflow:hidden;background:#0a0a0a}}
.sg .tw{{aspect-ratio:9/16}}
.tw img{{width:100%;height:100%;object-fit:cover;display:block;
  filter:grayscale(55%) brightness(.72);transition:filter .4s,transform .5s}}
.vc:hover .tw img{{filter:grayscale(0%) brightness(.92);transform:scale(1.07)}}
.po{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  background:rgba(0,0,0,.45);opacity:0;transition:opacity .3s}}
.vc:hover .po{{opacity:1}}
.pc{{width:44px;height:44px;border:1.5px solid var(--w);border-radius:50%;
  display:flex;align-items:center;justify-content:center}}
.pc svg{{margin-left:3px}}
.vi{{padding:12px 13px 14px;border-top:1px solid #161616}}
.vt{{font-family:'DM Serif Display',serif;font-size:13px;line-height:1.35;margin-bottom:7px;
  color:#bbb;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;transition:color .2s}}
.vc:hover .vt{{color:var(--w)}}
.vm{{display:flex;gap:9px;font-size:9px;letter-spacing:1.5px;color:#383838;text-transform:uppercase}}
.bs{{color:var(--gold)}}.bv{{color:#383838}}

/* section divider */
.sdiv{{display:flex;align-items:center;gap:18px;margin:56px 0 28px}}
.sdiv::before,.sdiv::after{{content:'';flex:1;height:1px;background:#161616}}
.sdiv span{{font-family:'Anton',sans-serif;font-size:13px;letter-spacing:4px;color:#2a2a2a;white-space:nowrap}}

/* footer */
footer{{position:relative;z-index:10;text-align:center;padding:36px 24px 70px;border-top:1px solid #0d0d0d}}
.flogo{{font-family:'Anton',sans-serif;font-size:32px;letter-spacing:6px;
  background:linear-gradient(180deg,#444,#1a1a1a);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:10px}}
.fsub{{font-size:9px;letter-spacing:3px;color:#1e1e1e;text-transform:uppercase}}
</style>
</head>
<body>
<div id="cur"></div>
<canvas id="bg"></canvas>
<div class="noise"></div>
<div class="sl"></div>

<!-- HERO -->
<section class="hero">

  <!-- Vinyl Stage -->
  <div class="vstage">
    <div class="vglow"></div>
    <div class="vdisk paused" id="vdisk">
      <!-- Label area = video thumbnail / iframe -->
      <div class="vlabel paused" id="vlabel">
        <div class="vlabel-inner paused" id="vlabelinner">
          <img id="vinylThumb" src="https://img.youtube.com/vi/{latest_id}/mqdefault.jpg" alt="Latest Video">
          <iframe id="vinylFrame"
            src=""
            allow="autoplay; encrypted-media"
            allowfullscreen>
          </iframe>
        </div>
      </div>
      <div class="vhole"></div>
    </div>
    <div class="tarm" id="tarm"></div>
  </div>

  <div class="latest-label">▶ SON VİDEO &nbsp;·&nbsp; <span>{latest_title[:40]}{"..." if len(latest_title)>40 else ""}</span></div>

  <!-- Title -->
  <div class="tblock">
    <div class="eye">Resmi Kanal · Official · 2026</div>
    <h1 class="mt" data-text="DJ Ne-Hot">DJ Ne-Hot</h1>
    <p class="sl2">Müzik · Ritim · Sokak</p>
    <div class="dline"></div>
    <div class="bwrap">
      <button class="pbtn" id="playBtn" onclick="toggleVinyl()">
        <svg id="pico" width="12" height="12" viewBox="0 0 12 12"><polygon points="2,1 10,6 2,11" fill="#000"/></svg>
        <span id="plbl">Vinyl'i Oynat</span>
      </button>
      <button class="pbtn pbtn2" onclick="openChannel()">↗ Kanala Git</button>
    </div>
  </div>

  <div class="shint"><span>Videolar</span><div class="sarr"></div></div>
</section>

<!-- STATS STRIP -->
<div class="strip">
  <div><strong>{fmt(subs)}</strong>Abone</div>
  <div><strong>{len(regular)}</strong>Video</div>
  <div><strong>{len(shorts)}</strong>Short</div>
  <div><strong>{len(videos)}</strong>Toplam</div>
</div>

<!-- VIDEOS -->
<section class="vsec" id="videos">
  <div class="shead">
    <div class="stitle">Videolar</div>
    <div class="scnt">{len(regular)} video</div>
  </div>
  <div class="utag">Son güncelleme: {updated}</div>
  <div class="sw">
    <span class="si">⌕</span>
    <input type="text" id="searchInput" placeholder="Video ara..." oninput="filterVideos(this.value)">
  </div>
  <div class="grid" id="videoGrid">
{reg_cards}
  </div>
{shorts_block}
</section>

<footer>
  <div class="flogo">DJ Ne-Hot</div>
  <div class="fsub">© 2026 · Tüm Hakları Saklıdır · djnehot.github.io/youtube</div>
</footer>

<script>
// ── CURSOR
const cur = document.getElementById('cur');
document.addEventListener('mousemove', e => {{ cur.style.left=e.clientX+'px'; cur.style.top=e.clientY+'px'; }});
document.querySelectorAll('a,button,.vc').forEach(el => {{
  el.addEventListener('mouseenter',()=>cur.classList.add('h'));
  el.addEventListener('mouseleave',()=>cur.classList.remove('h'));
}});

// ── CANVAS BACKGROUND
(()=>{{
  const c=document.getElementById('bg'), ctx=c.getContext('2d');
  let W,H,pts=[],lns=[];
  const resize=()=>{{ W=c.width=innerWidth; H=c.height=innerHeight; }};
  class Pt{{
    constructor(){{this.reset()}}
    reset(){{ this.x=Math.random()*W; this.y=Math.random()*H; this.r=Math.random()*1.1+.2;
      this.vx=(Math.random()-.5)*.25; this.vy=(Math.random()-.5)*.25; this.a=Math.random()*.35+.04; }}
    step(){{ this.x+=this.vx; this.y+=this.vy; if(this.x<0||this.x>W||this.y<0||this.y>H)this.reset(); }}
    draw(){{ ctx.beginPath();ctx.arc(this.x,this.y,this.r,0,Math.PI*2);ctx.fillStyle=`rgba(200,168,75,${{this.a}})`;ctx.fill(); }}
  }}
  class Ln{{
    constructor(){{this.reset()}}
    reset(){{ this.y=Math.random()*H; this.x=-200; this.w=Math.random()*280+60; this.s=Math.random()*.35+.08; this.a=Math.random()*.05+.01; }}
    step(){{ this.x+=this.s; if(this.x>W+200)this.reset(); }}
    draw(){{
      const g=ctx.createLinearGradient(this.x,0,this.x+this.w,0);
      g.addColorStop(0,'rgba(200,168,75,0)');g.addColorStop(.5,`rgba(200,168,75,${{this.a}})`);g.addColorStop(1,'rgba(200,168,75,0)');
      ctx.strokeStyle=g;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(this.x,this.y);ctx.lineTo(this.x+this.w,this.y);ctx.stroke();
    }}
  }}
  const init=()=>{{ resize(); pts=Array.from({{length:110}},()=>new Pt()); lns=Array.from({{length:16}},()=>new Ln()); }};
  const draw=()=>{{
    ctx.clearRect(0,0,W,H);
    const vig=ctx.createRadialGradient(W/2,H/2,H*.08,W/2,H/2,H*.85);
    vig.addColorStop(0,'rgba(0,0,0,0)');vig.addColorStop(1,'rgba(0,0,0,.75)');
    ctx.fillStyle=vig;ctx.fillRect(0,0,W,H);
    lns.forEach(l=>{{l.step();l.draw()}});
    pts.forEach(p=>{{p.step();p.draw()}});
    requestAnimationFrame(draw);
  }};
  addEventListener('resize',resize);
  init(); draw();
}})();

// ── VINYL + VIDEO
const LATEST_ID = '{latest_id}';
let playing = false;

function toggleVinyl() {{
  playing = !playing;
  const disk  = document.getElementById('vdisk');
  const lbl   = document.getElementById('vlabel');
  const linn  = document.getElementById('vlabelinner');
  const tarm  = document.getElementById('tarm');
  const plbl  = document.getElementById('plbl');
  const pico  = document.getElementById('pico');
  const thumb = document.getElementById('vinylThumb');
  const frame = document.getElementById('vinylFrame');

  if (playing) {{
    disk.classList.remove('paused');
    lbl.classList.remove('paused');
    linn.classList.remove('paused');
    tarm.classList.add('on');
    plbl.textContent = 'Durdur';
    pico.innerHTML = '<rect x="2" y="1" width="3" height="10" fill="#000"/><rect x="7" y="1" width="3" height="10" fill="#000"/>';
    // Load YouTube iframe inside vinyl
    frame.src = `https://www.youtube.com/embed/${{LATEST_ID}}?autoplay=1&mute=0&controls=0&loop=1&playlist=${{LATEST_ID}}&modestbranding=1&rel=0`;
    thumb.style.display = 'none';
    frame.style.display  = 'block';
  }} else {{
    disk.classList.add('paused');
    lbl.classList.add('paused');
    linn.classList.add('paused');
    tarm.classList.remove('on');
    plbl.textContent = "Vinyl'i Oynat";
    pico.innerHTML = '<polygon points="2,1 10,6 2,11" fill="#000"/>';
    frame.src = '';
    frame.style.display  = 'none';
    thumb.style.display  = 'block';
  }}
}}

// ── DEEP LINKS
const CH = '@djne-hot';
const CU = 'https://www.youtube.com/@djne-hot';
const isA = ()=>/Android/i.test(navigator.userAgent);
const isI = ()=>/iPhone|iPad|iPod/i.test(navigator.userAgent);

function openChannel() {{
  if(isA()) window.location.href=`intent://www.youtube.com/${{CH}}#Intent;scheme=https;package=com.google.android.youtube;S.browser_fallback_url=${{encodeURIComponent(CU)}};end`;
  else if(isI()) {{ window.location.href=`vnd.youtube://www.youtube.com/${{CH}}`; setTimeout(()=>window.location.href=CU,1500); }}
  else window.open(CU,'_blank');
}}

function playVideo(id, isShort) {{
  const web = isShort ? `https://www.youtube.com/shorts/${{id}}` : `https://www.youtube.com/watch?v=${{id}}`;
  if(isA()) window.location.href=`intent://www.youtube.com/${{isShort?'shorts/':'watch?v='}}${{id}}#Intent;scheme=https;package=com.google.android.youtube;S.browser_fallback_url=${{encodeURIComponent(web)}};end`;
  else if(isI()) {{ window.location.href=`vnd.youtube://${{id}}`; setTimeout(()=>window.location.href=web,1500); }}
  else window.open(web,'_blank');
}}

// ── SEARCH
function filterVideos(q) {{
  q = q.toLowerCase();
  document.querySelectorAll('.vc').forEach(c => {{
    c.style.display = c.querySelector('.vt').textContent.toLowerCase().includes(q) ? 'block' : 'none';
  }});
}}

// ── SCROLL REVEAL
const obs = new IntersectionObserver(entries => entries.forEach(e => {{
  if(e.isIntersecting) {{
    e.target.style.opacity='1';
    e.target.style.transform='translateY(0)';
  }}
}}), {{threshold:.06}});
document.querySelectorAll('.vc').forEach((el,i) => {{
  el.style.transition=`opacity .55s ${{i*.03}}s,transform .55s ${{i*.03}}s`;
  obs.observe(el);
}});
</script>
</body>
</html>"""


def main():
    print("="*50)
    print("  DJ Ne-Hot — YouTube Page Updater (GitHub Actions)")
    print("="*50)

    if not YOUTUBE_API_KEY:
        print("\n❌ Repo secret YOUTUBE_API_KEY is not set."); sys.exit(1)

    print("\n📡 Fetching channel info...")
    playlist_id, channel_title, subs = fetch_channel_info(YOUTUBE_API_KEY, CHANNEL_ID)
    print(f"   Channel : {channel_title}")
    print(f"   Subs    : {fmt(subs)}")

    latest_id = fetch_latest_video_id(YOUTUBE_API_KEY, playlist_id)
    last_seen = STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""
    if latest_id == last_seen and not FORCE:
        print(f"\n💤 No new upload (latest: {latest_id}). Nothing to do.")
        return
    print(f"\n🆕 Rebuilding (latest: {latest_id}, last seen: {last_seen or '-'}, force: {FORCE})")

    print("\n📹 Fetching ALL videos (all pages)...")
    videos = fetch_all_videos(YOUTUBE_API_KEY, playlist_id)
    print(f"   Raw count: {len(videos)} videos found")

    print("\n📊 Enriching with stats + Shorts detection...")
    videos = enrich_videos(YOUTUBE_API_KEY, videos)
    shorts = [v for v in videos if v.get("is_short")]
    reg    = [v for v in videos if not v.get("is_short")]
    print(f"   Regular : {len(reg)}  |  Shorts : {len(shorts)}")

    print("\n🎨 Generating fancy HTML...")
    html = generate_html(videos, subs)

    OUTPUT_FILE.write_text(html, encoding="utf-8")
    STATE_FILE.write_text(latest_id + "\n")
    print(f"   Saved → {OUTPUT_FILE}")

    # Commit message for the workflow step
    msg_file = os.environ.get("GITHUB_OUTPUT")
    if msg_file:
        now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        with open(msg_file, "a", encoding="utf-8") as f:
            f.write(f"message=Auto update: {len(videos)} videos [{now}]\n")

    print(f"\n✅ Built. The workflow will push it → https://djnehot.github.io/youtube/")
    print("="*50)


if __name__ == "__main__":
    main()
