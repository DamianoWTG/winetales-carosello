# -*- coding: utf-8 -*-
"""
WineTales - Carosello Web App (Flask, per Render.com free)
----------------------------------------------------------
Il collega apre il link da iPhone/iPad/PC, tocca "Genera carosello" e ottiene
le card pronte da salvare in Foto (+ zip). Tutto gira sul server: nessun
problema di CORS, nessuna installazione lato utente.
Gli asset (font/logo/calice) sono incorporati in assets_b64.py.
"""

import os
import re
import ssl
import html
import json
import uuid
import zipfile
import tempfile
import datetime
import urllib.request
from io import BytesIO

from flask import Flask, jsonify, send_from_directory, Response, request
from PIL import Image, ImageDraw, ImageFont

from assets_b64 import FONT_ROMAN, FONT_ITALIC, LOGO_PNG, GLASS_PNG

# ============================ CONFIG ============================
SITE          = "https://winetalesmagazine.com"
DA_LUNEDI     = True                 # lun->sab della settimana (pubblicazione domenica)
DAYS_BACK     = 7
MAX_POSTS     = 30
ESCLUDI_RUBRICHE = ["Blend News"]
HOOK_PRIMA_FRASE = True
HOOK_MAXLEN   = 150
OLDEST_FIRST  = True

# ------------------------- STILE -------------------------
W, H = 1080, 1920
BG          = (0, 0, 0)
WHITE       = (245, 242, 236)
BORDEAUX    = (140, 28, 44)
ITEMS_PER_SUMMARY = 5
HOOK_MAXLINES = 5
ART_TESTO_TOP = 0.50

MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

OUTDIR = os.path.join(tempfile.gettempdir(), "wt_carosello")
os.makedirs(OUTDIR, exist_ok=True)


# ============================ RETE ============================
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _open(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        "Referer": SITE + "/",
    })
    try:
        return urllib.request.urlopen(req, timeout=45)
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            return urllib.request.urlopen(req, timeout=45, context=ssl._create_unverified_context())
        raise


def filter_posts(posts):
    """Filtra i post (arrivati dal browser del collega) sulla settimana lun->sab,
    esclude le rubriche indesiderate, ordina dal piu' vecchio."""
    oggi = datetime.date.today()
    if DA_LUNEDI:
        inizio = oggi - datetime.timedelta(days=oggi.weekday())   # lunedi'
        fine = inizio + datetime.timedelta(days=5)                # sabato
    else:
        inizio = oggi - datetime.timedelta(days=DAYS_BACK)
        fine = oggi
    a, b = inizio.isoformat(), fine.isoformat()

    def in_finestra(p):
        d = (p.get("date") or "")[:10]
        return bool(d) and a <= d <= b

    sel = [p for p in posts if in_finestra(p)]
    esc = {e.strip().lower() for e in ESCLUDI_RUBRICHE}
    sel = [p for p in sel if category_of(p).strip().lower() not in esc]
    sel.sort(key=lambda p: p.get("date", ""))     # dal piu' vecchio
    return sel[:MAX_POSTS]


def fetch_image(url):
    try:
        with _open(url) as r:
            return Image.open(BytesIO(r.read())).convert("RGB")
    except Exception:
        return None


# ============================ TESTO ============================
def clean_text(raw):
    t = re.sub(r"<[^>]+>", " ", raw or "")
    t = html.unescape(t)
    t = t.replace("\xa0", " ")
    t = re.sub(r"\[[^\]]*\]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_hook(raw):
    t = clean_text(raw)
    t = t.replace("…", " ")
    t = re.sub(r"Continua a leggere.*$", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    if HOOK_PRIMA_FRASE:
        m = re.search(r"\.(?=\s|$)", t)
        if m and 25 <= (m.start() + 1) <= HOOK_MAXLEN:
            return t[:m.start() + 1].strip()
    t = t.strip(" .,;:-")
    if len(t) > HOOK_MAXLEN:
        t = t[:HOOK_MAXLEN].rsplit(" ", 1)[0].rstrip(" .,;:-") + "…"
    return t


CAT_DISPLAY = {"BlendNews": "Blend News", "Vinodentro": "Vino dentro"}


def category_of(post):
    for group in (post.get("_embedded", {}).get("wp:term", []) or []):
        for t in group:
            if t.get("taxonomy") == "category" and t.get("name"):
                return CAT_DISPLAY.get(t["name"], t["name"])
    return ""


def best_image_url(post):
    media = (post.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
    sizes = (media.get("media_details", {}) or {}).get("sizes", {}) or {}
    best_w, best_url = -1, None
    for s in sizes.values():
        w = s.get("width") or 0
        if w > best_w and s.get("source_url"):
            best_w, best_url = w, s["source_url"]
    return best_url or media.get("source_url") or ""


# ============================ RENDER ============================
def font(size, weight="Regular", italic=False):
    f = ImageFont.truetype(BytesIO(FONT_ITALIC if italic else FONT_ROMAN), size)
    try:
        f.set_variation_by_name((weight + " Italic") if italic and weight != "Regular"
                                else ("Italic" if italic else weight))
    except Exception:
        pass
    return f


def load_logo(width):
    lg = Image.open(BytesIO(LOGO_PNG)).convert("RGBA")
    return lg.resize((width, int(lg.height * width / lg.width)), Image.LANCZOS)


def load_glass(width):
    g = Image.open(BytesIO(GLASS_PNG)).convert("RGBA")
    return g.resize((width, int(g.height * width / g.width)), Image.LANCZOS)


def paste_glass(img, cx, cy, width):
    g = load_glass(width)
    img.paste(g, (int(cx - g.width / 2), int(cy - g.height / 2)), g)


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def wrap_title(draw, text, fnt, max_w):
    lines = []
    for seg in [s.strip() for s in re.split(r'(?<=\.)\s+', text) if s.strip()]:
        lines.extend(wrap(draw, seg, fnt, max_w))
    return lines


def text_center(draw, cx, y, text, fnt, fill, tracking=0):
    if tracking:
        total = sum(draw.textlength(c, font=fnt) for c in text) + tracking * (len(text) - 1)
        x = cx - total / 2
        for c in text:
            draw.text((x, y), c, font=fnt, fill=fill)
            x += draw.textlength(c, font=fnt) + tracking
    else:
        draw.text((cx - draw.textlength(text, font=fnt) / 2, y), text, font=fnt, fill=fill)


def logo_top(img, draw, width=560, y=140, alpha=255):
    lg = load_logo(width)
    if alpha < 255:
        lg.putalpha(lg.split()[3].point(lambda p: int(p * alpha / 255)))
    img.paste(lg, ((W - width) // 2, y), lg)
    return y + lg.height


def daterange_label(rows):
    ds = sorted(r["data"] for r in rows if r.get("data"))
    def fmt(s):
        y, m, d = s.split("-"); return f"{int(d)} {MESI[int(m)].upper()} {y}"
    if not ds:
        return datetime.date.today().strftime("%d/%m/%Y")
    if ds[0] == ds[-1]:
        return fmt(ds[0])
    a, b = ds[0].split("-"), ds[-1].split("-")
    if a[1] == b[1]:
        return f"{int(a[2])} - {int(b[2])} {MESI[int(b[1])].upper()} {b[0]}"
    return f"{fmt(ds[0])} - {fmt(ds[-1])}"


def cover_fit(im):
    im = im.convert("RGB")
    r = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * r), int(im.height * r)), Image.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def card_copertina(rows):
    img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
    y = logo_top(img, d, 600, 250)
    d.line([(W/2-70, y+70), (W/2+70, y+70)], fill=BORDEAUX, width=3)
    text_center(d, W/2, y+120, "I principali articoli", font(74, italic=True), WHITE)
    text_center(d, W/2, y+210, "della settimana", font(74, italic=True), WHITE)
    text_center(d, W/2, y+330, daterange_label(rows), font(30, "Medium"), WHITE, tracking=4)
    paste_glass(img, W/2, H-360, 130)
    text_center(d, W/2, H-250, "WINETALESMAGAZINE.COM", font(30, "Medium"), WHITE, tracking=3)
    return img


def card_sommario(rows, start):
    img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
    y = logo_top(img, d, 480, 150)
    d.line([(W/2-60, y+55), (W/2+60, y+55)], fill=BORDEAUX, width=3)
    text_center(d, W/2, y+95, "Sommario della settimana", font(50), WHITE)
    y0, x_num, x_txt = y+230, 110, 250
    fn_num, fn_ttl, fn_rub = font(64, "Medium"), font(42, "SemiBold"), font(32, italic=True)
    block = rows[start:start+ITEMS_PER_SUMMARY]
    row_h = (H - y0 - 160) / max(len(block), 1)
    for i, r in enumerate(block):
        n = start + i + 1
        cy = y0 + i * row_h
        d.text((x_num, cy), f"{n:02d}", font=fn_num, fill=BORDEAUX)
        all_lines = wrap_title(d, r["titolo"], fn_ttl, W - x_txt - 90)
        tl = all_lines[:2]
        if len(all_lines) > 2 and tl:
            last = tl[-1]
            while last and d.textlength(last + "…", font=fn_ttl) > W - x_txt - 90:
                last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
            tl[-1] = last + "…"
        ty = cy + (0 if len(tl) == 2 else 8)
        for line in tl:
            d.text((x_txt, ty), line, font=fn_ttl, fill=WHITE); ty += 48
        if r.get("categoria"):
            d.text((x_txt, ty+2), r["categoria"], font=fn_rub, fill=BORDEAUX)
    text_center(d, W/2, H-120, "winetalesmagazine.com", font(28, "Medium"), WHITE, tracking=3)
    return img


def card_articolo(r, pil):
    img = cover_fit(pil) if pil is not None else Image.new("RGB", (W, H), (30, 22, 24))
    scrim = Image.new("L", (1, H))
    for yy in range(H):
        scrim.putpixel((0, yy), min(230, 90 + int(150 * max(0, (yy - H*0.30) / (H*0.70)))))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, scrim.resize((W, H)))
    d = ImageDraw.Draw(img)
    logo_top(img, d, 460, 120, alpha=235)
    LEFT, RIGHT = 90, 120
    fn_ttl, fn_hook, hook_lh = font(72, "SemiBold"), font(50), 70
    tx = LEFT + 96
    title_lines = wrap_title(d, r["titolo"], fn_ttl, W - tx - RIGHT)
    hook_lines = wrap(d, r["hook"], fn_hook, W - tx - RIGHT)[:HOOK_MAXLINES]
    th, hh = len(title_lines) * 82, len(hook_lines) * hook_lh
    y = min(int(H * ART_TESTO_TOP), H - 130 - hh - 58 - th)
    paste_glass(img, LEFT + 32, y + 54, 76)
    ty = y
    for line in title_lines:
        d.text((tx, ty), line, font=fn_ttl, fill=WHITE); ty += 82
    hy = ty + 58
    for line in hook_lines:
        d.text((tx, hy), line, font=fn_hook, fill=WHITE); hy += hook_lh
    return img


def card_chiusura():
    img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
    logo_top(img, d, 520, 210)
    text_center(d, W/2, H/2-120, "Buona lettura", font(96, italic=True), WHITE)
    d.line([(W/2-90, H/2+10), (W/2+90, H/2+10)], fill=BORDEAUX, width=3)
    text_center(d, W/2, H/2+60, "WINETALESMAGAZINE.COM", font(34, "Medium"), WHITE, tracking=3)
    paste_glass(img, W/2, H/2+200, 140)
    return img


def slug(t, n):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", t.lower()).strip("-")[:32]
    return f"{n:02d}_{s or 'articolo'}"


def genera_cards(posts_json):
    posts = filter_posts(posts_json)
    if not posts:
        return None, "Nessun articolo pubblicato questa settimana (o erano tutte Blend News)."

    rows = []
    foto_ok = 0
    for p in posts:
        im = fetch_image(best_image_url(p))
        if im is not None:
            foto_ok += 1
        rows.append({
            "titolo": clean_text(p["title"]["rendered"]),
            "hook": clean_hook(p["excerpt"]["rendered"]),
            "categoria": category_of(p),
            "data": p.get("date", "")[:10],
            "img": im,
        })

    cards = [("00_copertina.png", card_copertina(rows))]
    n = 1
    for s in range(0, len(rows), ITEMS_PER_SUMMARY):
        cards.append((f"{n:02d}_sommario.png", card_sommario(rows, s))); n += 1
    for i, r in enumerate(rows, 1):
        cards.append((f"{n:02d}_" + slug(r["titolo"], i) + ".png", card_articolo(r, r["img"]))); n += 1
    cards.append((f"{n:02d}_chiusura.png", card_chiusura()))

    uid = uuid.uuid4().hex[:10]
    d = os.path.join(OUTDIR, uid)
    os.makedirs(d, exist_ok=True)
    names = []
    for name, im in cards:
        im.save(os.path.join(d, name))
        names.append(name)
    with zipfile.ZipFile(os.path.join(d, "carosello.zip"), "w") as z:
        for name in names:
            z.write(os.path.join(d, name), name)

    return {"id": uid, "names": names, "count": len(names), "articoli": len(rows),
            "foto_ok": foto_ok, "range": daterange_label(rows)}, None


# ============================ WEB ============================
app = Flask(__name__)

PAGE = """<!doctype html><html lang="it"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wine Tales — Carosello</title>
<style>
 :root{--bord:#8c1c2c}
 *{box-sizing:border-box}
 body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#111;color:#f5f2ec;
      text-align:center;padding:18px}
 h1{font-weight:600;font-size:22px;margin:8px 0 4px}
 p.sub{color:#bbb;font-size:14px;margin:0 0 18px}
 button{background:var(--bord);color:#fff;border:0;border-radius:12px;padding:16px 22px;
        font-size:18px;font-weight:600;width:100%;max-width:420px;cursor:pointer}
 button:disabled{opacity:.6}
 #msg{margin:16px 0;color:#ddd;font-size:15px;min-height:22px}
 .grid{display:flex;flex-direction:column;align-items:center;gap:16px;margin-top:10px}
 .grid img{width:100%;max-width:420px;border-radius:10px;box-shadow:0 2px 12px #0008}
 a.zip{display:inline-block;margin:18px auto;color:#f5f2ec;border:1px solid #555;
       border-radius:10px;padding:12px 18px;text-decoration:none;font-size:15px}
 .hint{color:#999;font-size:13px;margin-top:6px}
 .spin{width:26px;height:26px;border:3px solid #444;border-top-color:var(--bord);
       border-radius:50%;animation:s 1s linear infinite;display:inline-block;vertical-align:middle}
 @keyframes s{to{transform:rotate(360deg)}}
</style></head><body>
 <h1>🍷 Wine Tales — Carosello</h1>
 <p class="sub">Articoli della settimana (lun→sab). Tocca Genera.</p>
 <button id="go" onclick="genera()">✨ Genera carosello</button>
 <div id="msg"></div>
 <div id="out"></div>
<script>
var API="https://winetalesmagazine.com/wp-json/wp/v2/posts?per_page=30&orderby=date&order=desc&_embed=1";
async function genera(){
 var b=document.getElementById('go'), m=document.getElementById('msg'), o=document.getElementById('out');
 b.disabled=true; o.innerHTML=''; m.innerHTML='<span class="spin"></span> &nbsp;Leggo gli articoli…';
 try{
  // 1) i dati li scarica il TUO browser dal sito (non il server)
  var pr=await fetch(API,{headers:{'Accept':'application/json'}});
  if(!pr.ok){ m.textContent='Il sito non risponde ('+pr.status+'). Riprova.'; b.disabled=false; return; }
  var posts=await pr.json();
  // 2) il server disegna le card
  m.innerHTML='<span class="spin"></span> &nbsp;Creo le card… (~1 minuto)';
  var r=await fetch('genera',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({posts:posts})});
  var j=await r.json();
  if(!j.ok){ m.textContent = j.error||'Errore'; b.disabled=false; return; }
  m.textContent = j.count+' card pronte ('+j.articoli+' articoli, '+j.foto_ok+' foto) — '+j.range;
  var h='<a class="zip" href="zip/'+j.id+'">⬇︎ Scarica tutte (ZIP)</a>';
  h+='<p class="hint">Per salvarle: tieni premuto su ogni immagine → “Aggiungi a Foto”.</p>';
  h+='<div class="grid">';
  for(const n of j.names){ h+='<img loading="lazy" src="img/'+j.id+'/'+n+'">'; }
  h+='</div>';
  o.innerHTML=h;
 }catch(e){ m.textContent='Errore: '+e; }
 b.disabled=false;
}
</script>
</body></html>"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/genera", methods=["POST"])
def genera_route():
    try:
        body = request.get_json(force=True, silent=True) or {}
        posts = body.get("posts")
        if not isinstance(posts, list):
            return jsonify({"ok": False, "error": "Dati articoli mancanti dal browser."})
        data, err = genera_cards(posts)
        if err:
            return jsonify({"ok": False, "error": err})
        return jsonify({"ok": True, **data})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Errore: {e}"})


@app.route("/img/<uid>/<name>")
def img(uid, name):
    d = os.path.join(OUTDIR, uid)
    if not os.path.isfile(os.path.join(d, name)):
        return "not found", 404
    return send_from_directory(d, name, mimetype="image/png")


@app.route("/zip/<uid>")
def zip_route(uid):
    d = os.path.join(OUTDIR, uid)
    if not os.path.isfile(os.path.join(d, "carosello.zip")):
        return "not found", 404
    return send_from_directory(d, "carosello.zip", as_attachment=True,
                               download_name="carosello.zip")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
