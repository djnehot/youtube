"""
DJ Ne-Hot — website builder (runs in GitHub Actions)
- Fetches ALL videos + shorts from the YouTube Data API
- Fills .github/scripts/template.html and writes index.html
- Only rebuilds when there is a new upload (unless FORCE=true)
"""

import html, json, os, re, subprocess, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────
#  CONFIGURATION
#  The API key comes from the repo secret YOUTUBE_API_KEY (never committed).
# ─────────────────────────────────────────────
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
CHANNEL_ID      = os.environ.get("CHANNEL_ID", "UCSEZH_CUNJgjVHJb0jRUvcQ")
CHANNEL_HANDLE  = "@djne-hot"
SITE_URL        = "https://djnehot.github.io/youtube/"
TAGLINE         = "Müzik · Ritim · Sokak"

# Optional links — leave "" to hide. They appear in the header, contact section and footer.
BOOKING_EMAIL   = ""          # e.g. "booking@example.com"
SOCIAL_LINKS    = {           # name -> URL
    "Instagram": "",
    "TikTok":    "",
    "Spotify":   "",
}

SCRIPT_DIR  = Path(__file__).resolve().parent
REPO_ROOT   = SCRIPT_DIR.parents[1]
TEMPLATE    = SCRIPT_DIR / "template.html"
OUTPUT_FILE = Path(os.environ.get("OUTPUT_FILE", REPO_ROOT / "index.html"))
STATE_FILE  = REPO_ROOT / ".github" / "last_video_id.txt"
FORCE       = os.environ.get("FORCE", "").lower() == "true"
TZ          = ZoneInfo("Europe/Istanbul")
TR_MONTHS   = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
# ─────────────────────────────────────────────

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

API = "https://www.googleapis.com/youtube/v3"


def api_get(path, **params):
    r = requests.get(f"{API}/{path}", params={**params, "key": YOUTUBE_API_KEY}, timeout=30)
    r.raise_for_status()
    return r.json()


# ── YouTube data ─────────────────────────────

def fetch_channel():
    items = api_get("channels", part="contentDetails,snippet,statistics,brandingSettings",
                    id=CHANNEL_ID).get("items", [])
    if not items:
        raise ValueError("Channel not found. Check CHANNEL_ID.")
    it = items[0]
    sn, st = it["snippet"], it.get("statistics", {})
    thumbs = sn.get("thumbnails", {})
    banner = it.get("brandingSettings", {}).get("image", {}).get("bannerExternalUrl", "")
    return {
        "uploads":     it["contentDetails"]["relatedPlaylists"]["uploads"],
        "title":       sn.get("title", "DJ Ne-Hot"),
        "description": sn.get("description", "").strip(),
        "avatar":      (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
        "banner":      f"{banner}=w2120-fcrop64=1,00005a57ffffa5a8-k-c0xffffffff-no-nd-rj" if banner else "",
        "subs":        int(st.get("subscriberCount", 0)),
        "views":       int(st.get("viewCount", 0)),
    }


def fetch_latest_video_id(playlist_id):
    """Cheap check (1 quota unit): newest upload's ID."""
    items = api_get("playlistItems", part="contentDetails", playlistId=playlist_id,
                    maxResults=1).get("items", [])
    return items[0]["contentDetails"]["videoId"] if items else ""


def fetch_all_videos(playlist_id):
    videos, token, page = [], None, 0
    while True:
        page += 1
        print(f"   Fetching page {page}...")
        params = {"part": "snippet", "playlistId": playlist_id, "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = api_get("playlistItems", **params)
        for item in data.get("items", []):
            sn = item["snippet"]
            vid = sn["resourceId"]["videoId"]
            if sn.get("title") in ("Private video", "Deleted video"):
                continue
            videos.append({"id": vid, "title": sn.get("title", ""), "published": sn.get("publishedAt", "")})
        token = data.get("nextPageToken")
        if not token:
            return videos


def enrich_videos(videos):
    """Add duration, Shorts flag, views and likes (50 per request)."""
    extra = {}
    ids = [v["id"] for v in videos]
    for i in range(0, len(ids), 50):
        data = api_get("videos", part="contentDetails,statistics", id=",".join(ids[i:i + 50]))
        for it in data.get("items", []):
            secs = iso_seconds(it["contentDetails"]["duration"])
            st = it.get("statistics", {})
            extra[it["id"]] = {"secs": secs, "is_short": 0 < secs <= 61,
                               "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0))}
    # drop videos the API didn't return (private / removed)
    return [{**v, **extra[v["id"]]} for v in videos if v["id"] in extra]


def iso_seconds(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


# ── formatting helpers ───────────────────────

def fmt_num(n):
    # Turkish style, like YouTube TR: 1,2 Mn / 3,4 B (bin)
    if n >= 1_000_000: return f"{n / 1_000_000:.1f} Mn".replace(".0 ", " ").replace(".", ",")
    if n >= 1_000:     return f"{n / 1_000:.1f} B".replace(".0 ", " ").replace(".", ",")
    return str(n)


def fmt_dur(secs):
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_date(iso):
    if not iso:
        return ""
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)
    return f"{d.day} {TR_MONTHS[d.month - 1]} {d.year}"


def esc(s):
    return html.escape(s or "", quote=True)


def thumb(vid, quality="hqdefault"):
    return f"https://i.ytimg.com/vi/{vid}/{quality}.jpg"


def watch_url(v):
    return f"https://www.youtube.com/shorts/{v['id']}" if v["is_short"] else f"https://www.youtube.com/watch?v={v['id']}"


# ── HTML pieces ──────────────────────────────

def video_card(v, extra_class=""):
    t = esc(v["title"])
    return (
        f'<a class="card {extra_class}" href="{watch_url(v)}" target="_blank" rel="noopener" '
        f'data-id="{v["id"]}" data-short="0" data-views="{v["views"]}" '
        f'data-date="{esc(v["published"])}">'
        f'<div class="card-media"><img src="{thumb(v["id"], "mqdefault")}" alt="" loading="lazy" width="320" height="180">'
        f'<span class="dur">{fmt_dur(v["secs"])}</span><span class="play" aria-hidden="true"></span></div>'
        f'<div class="card-body"><h3 class="card-title">{t}</h3>'
        f'<p class="card-meta">{fmt_num(v["views"])} izlenme · {fmt_date(v["published"])}</p></div></a>'
    )


def short_card(v):
    t = esc(v["title"])
    return (
        f'<a class="short" href="{watch_url(v)}" target="_blank" rel="noopener" '
        f'data-id="{v["id"]}" data-short="1" title="{t}">'
        f'<img src="{thumb(v["id"], "hqdefault")}" alt="" loading="lazy" width="360" height="640">'
        f'<div class="short-info"><p class="short-title">{t}</p>'
        f'<p class="short-views">{fmt_num(v["views"])} izlenme</p></div></a>'
    )


def link_buttons(ch_url):
    links = [("YouTube", ch_url)] + [(k, u) for k, u in SOCIAL_LINKS.items() if u]
    return "".join(f'<a class="chip" href="{esc(u)}" target="_blank" rel="noopener">{esc(k)}</a>' for k, u in links)


def build_html(ch, videos):
    regular = [v for v in videos if not v["is_short"]]
    shorts  = [v for v in videos if v["is_short"]]
    latest  = regular[0] if regular else (videos[0] if videos else None)
    popular = sorted(regular, key=lambda v: v["views"], reverse=True)[:4]
    ch_url  = f"https://www.youtube.com/{CHANNEL_HANDLE}"
    now     = datetime.now(TZ)

    desc = ch["description"] or "DJ Ne-Hot resmi web sitesi. Tüm müzik videoları, Shorts ve son çıkanlar burada."
    about_html = "<br>".join(esc(line) for line in desc[:900].splitlines() if line.strip())

    contact = ""
    if BOOKING_EMAIL:
        contact = (f'<a class="btn btn-primary" href="mailto:{esc(BOOKING_EMAIL)}">Booking &amp; İletişim</a>'
                   f'<p class="contact-mail">{esc(BOOKING_EMAIL)}</p>')

    # blurred cover of the newest video as hero backdrop (channel banners crop badly)
    hero_bg = thumb(latest["id"], "hqdefault") if latest else ch["banner"]
    jsonld = {
        "@context": "https://schema.org", "@type": "Person", "name": "DJ Ne-Hot",
        "url": SITE_URL, "image": SITE_URL + "assets/logo.webp", "jobTitle": "DJ",
        "sameAs": [ch_url] + [u for u in SOCIAL_LINKS.values() if u],
    }

    values = {
        "TITLE":          "DJ Ne-Hot — Resmi Web Sitesi",
        "META_DESC":      esc(desc.splitlines()[0][:155] if desc else TAGLINE),
        "SITE_URL":       SITE_URL,
        "OG_IMAGE":       thumb(latest["id"], "maxresdefault") if latest else ch["avatar"],
        "JSONLD":         json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/"),
        "AVATAR":         esc(ch["avatar"]),
        "HERO_BG":        esc(hero_bg),
        "TAGLINE":        esc(TAGLINE),
        "CHANNEL_URL":    ch_url,
        "SUB_URL":        f"{ch_url}?sub_confirmation=1",
        "STAT_SUBS":      fmt_num(ch["subs"]),
        "STAT_VIEWS":     fmt_num(ch["views"] or sum(v["views"] for v in videos)),
        "STAT_VIDEOS":    str(len(regular)),
        "STAT_SHORTS":    str(len(shorts)),
        "LATEST_ID":      latest["id"] if latest else "",
        "LATEST_SHORT":   "1" if latest and latest["is_short"] else "0",
        "LATEST_URL":     watch_url(latest) if latest else ch_url,
        "LATEST_TITLE":   esc(latest["title"]) if latest else "",
        "LATEST_META":    f'{fmt_num(latest["views"])} izlenme · {fmt_date(latest["published"])}' if latest else "",
        "LATEST_THUMB":   thumb(latest["id"], "maxresdefault") if latest else "",
        "POPULAR_CARDS":  "\n".join(video_card(v, "card-lg") for v in popular),
        "VIDEO_CARDS":    "\n".join(video_card(v) for v in regular),
        "SHORT_CARDS":    "\n".join(short_card(v) for v in shorts),
        "SHORTS_DISPLAY": "" if shorts else "hidden",
        "ABOUT":          about_html,
        "LINKS":          link_buttons(ch_url),
        "CONTACT":        contact,
        "UPDATED":        f"{now.day} {TR_MONTHS[now.month - 1]} {now.year} · {now:%H:%M}",
        "YEAR":           str(now.year),
    }
    out = TEMPLATE.read_text(encoding="utf-8")
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if left:
        raise ValueError(f"Unfilled template fields: {set(left)}")
    return out


# ── main ─────────────────────────────────────

def main():
    print("=" * 50)
    print("  DJ Ne-Hot — website builder")
    print("=" * 50)
    if not YOUTUBE_API_KEY:
        print("\n❌ YOUTUBE_API_KEY is not set."); sys.exit(1)

    print("\n📡 Fetching channel info...")
    ch = fetch_channel()
    print(f"   Channel : {ch['title']}  |  Subs: {fmt_num(ch['subs'])}")

    latest_id = fetch_latest_video_id(ch["uploads"])
    last_seen = STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""
    if latest_id == last_seen and not FORCE:
        print(f"\n💤 No new upload (latest: {latest_id}). Nothing to do.")
        return
    print(f"\n🆕 Rebuilding (latest: {latest_id}, last seen: {last_seen or '-'}, force: {FORCE})")

    print("\n📹 Fetching all videos...")
    videos = enrich_videos(fetch_all_videos(ch["uploads"]))
    shorts = sum(v["is_short"] for v in videos)
    print(f"   Raw count: {len(videos)} videos found")
    print(f"   Regular : {len(videos) - shorts}  |  Shorts : {shorts}")

    OUTPUT_FILE.write_text(build_html(ch, videos), encoding="utf-8")
    print(f"   Saved → {OUTPUT_FILE}")
    if "OUTPUT_FILE" not in os.environ:
        STATE_FILE.write_text(latest_id + "\n")

    msg_file = os.environ.get("GITHUB_OUTPUT")
    if msg_file:
        with open(msg_file, "a", encoding="utf-8") as f:
            f.write(f"message=Auto update: {len(videos)} videos [{datetime.now(TZ):%Y-%m-%d %H:%M}]\n")
    print(f"\n✅ Built → {SITE_URL}")


if __name__ == "__main__":
    main()
