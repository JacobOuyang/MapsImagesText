"""Find real pictures on the web: instructional diagrams and photos, with their licence and author.

    imagesearch.search("CPR hand position chest compressions")   # -> [{"title", "url", "page", "license", "creator", "source"}]
    imagesearch.fetch(result)                                     # -> PIL image (cached in cache/web/)

Two keyless sources, searched together: Wikimedia Commons (first-aid and safety diagrams, CC0 / CC BY-SA) and
Openverse (openly licensed photos from Flickr and others). Pictures under a "no derivatives" licence are skipped,
because they get redrawn as text; every picture used is credited. When those find too little, brave() searches the
whole web (Brave Search's image API, BRAVE_API_KEY in .env): far more pictures, but their rights are unknown, so they
come after the openly licensed ones, watermarked stock sites are skipped, and the site is the credit.
"""
import hashlib
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

from . import llm  # noqa: F401  (loads .env)

UA = {"User-Agent": "MapsImagesText/0.1 (low-data emergency messaging prototype)"}
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "web")
TIMEOUT = 20
WIDTH = 800


def _clean(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def commons(query, n=8):
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=TIMEOUT, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": n, "gsrsearch": query,
        "prop": "imageinfo", "iiprop": "url|extmetadata|mime", "iiurlwidth": WIDTH})
    r.raise_for_status()
    pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        if not str(ii.get("mime", "")).startswith("image/") or ii.get("mime") == "image/svg+xml" and not ii.get("thumburl"):
            continue
        out.append({"title": p["title"].replace("File:", "").rsplit(".", 1)[0], "url": ii.get("thumburl") or ii.get("url"),
                    "page": ii.get("descriptionurl"), "license": _clean((meta.get("LicenseShortName") or {}).get("value")),
                    "creator": _clean((meta.get("Artist") or {}).get("value"))[:60], "source": "Wikimedia Commons"})
    return out


def openverse(query, n=8):
    r = requests.get("https://api.openverse.org/v1/images/", headers=UA, timeout=TIMEOUT / 2,
                     params={"q": query, "page_size": n})
    r.raise_for_status()
    return [{"title": x.get("title") or "", "url": x.get("thumbnail") or x["url"], "page": x.get("foreign_landing_url"),
             "license": ("CC %s %s" % (x.get("license", ""), x.get("license_version") or "")).upper().strip()
             if x.get("license") not in ("cc0", "pdm") else x.get("license", "").upper(),
             "creator": (x.get("creator") or "")[:60], "source": x.get("source") or "Openverse"} for x in r.json().get("results", [])]


STOCK = ("gettyimages", "istockphoto", "shutterstock", "alamy", "dreamstime", "123rf", "depositphotos",
         "stock.adobe", "vectorstock", "bigstockphoto", "pixtastock", "canstockphoto")  # watermarked, not licensed


def brave(query, n=8):
    """Brave Search image results (needs BRAVE_API_KEY), without watermarked stock sites."""
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return []
    r = requests.get("https://api.search.brave.com/res/v1/images/search", timeout=TIMEOUT, params={
        "q": query, "count": n, "safesearch": "strict"}, headers={"Accept": "application/json", "X-Subscription-Token": key})
    r.raise_for_status()
    out = []
    for x in r.json().get("results", []):
        url = (x.get("properties") or {}).get("url") or (x.get("thumbnail") or {}).get("src")
        site = x.get("source") or ""
        if url and not any(st in (site + url).lower() for st in STOCK):
            out.append({"title": x.get("title") or "", "url": url, "page": x.get("url"), "license": "rights unknown",
                        "creator": site, "source": "web (Brave Search)"})
    return out


def search(query, n=8):
    """Both sources, Commons first, no 'no derivatives' licences, no duplicates. Never raises. Commons wants every
    word to match, so a long query that finds little is retried shorter ('CPR hand position chest compressions
    diagram' found nothing; 'CPR hand position' found 6)."""
    words = query.split()
    out = []
    for k in dict.fromkeys([len(words), 4, 3, 2]):
        if k > len(words) or k < 1:
            continue
        for x in _search(" ".join(words[:k]), n):
            if x["url"] not in {o["url"] for o in out}:
                out.append(x)
        if len(out) >= 4:
            break
    return out


def _search(query, n):
    out, seen = [], set()
    with ThreadPoolExecutor(2) as pool:
        got = [pool.submit(f, query, n) for f in (commons, openverse)]
        for g in got:
            try:
                for x in g.result():
                    if x["url"] and x["url"] not in seen and "ND" not in x["license"].upper().replace("-", " ").split():
                        seen.add(x["url"])
                        out.append(x)
            except Exception:
                pass
    return out


def fetch(result):
    """The picture, at most WIDTH wide, cached by URL. -> PIL image (RGB) or None."""
    key = hashlib.md5(result["url"].encode()).hexdigest()
    path = os.path.join(CACHE_DIR, key + ".jpg")
    if os.path.exists(path):
        return Image.open(path).convert("RGB")
    try:
        r = requests.get(result["url"], headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        if im.mode in ("RGBA", "LA", "P"):  # diagrams with transparency: put them on white
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, "white")
            bg.alpha_composite(im)
            im = bg
        im = im.convert("RGB")
        im.thumbnail((WIDTH, WIDTH))
    except Exception:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    im.save(path, quality=90)
    return im


def credit(result):
    who = result.get("creator") or "unknown"
    return "%s, %s, %s" % (who, result.get("license") or "licence unknown", result.get("source"))
