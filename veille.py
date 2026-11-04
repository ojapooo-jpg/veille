#!/usr/bin/env python3
"""Veille actu automatique — collecte Google News + flux RSS, filtre par mots-clés,
classe par score, supprime les doublons et génère un rapport HTML + archive CSV.
Usage : python veille.py            (utilise config.json à côté du script)
        python veille.py --ouvrir   (ouvre le rapport dans le navigateur à la fin)
Aucune dépendance : Python 3.8+ suffit."""
import csv, html, json, os, re, sys, unicodedata, urllib.parse, urllib.request, webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path

ICI = Path(__file__).resolve().parent
CFG = json.loads((ICI / "config.json").read_text(encoding="utf-8"))
EN_LIGNE = bool(os.environ.get("GITHUB_ACTIONS"))
SORTIE = ICI / ("docs" if EN_LIGNE else "rapports"); SORTIE.mkdir(exist_ok=True)
DEJA_VUS = ICI / "deja_vus.json"
ARCHIVE = ICI / "archive.csv"
UA = {"User-Agent": "Mozilla/5.0 (veille-actu)"}
POIDS = {"forts": 3, "moyens": 2, "judiciaire": 1}

def norm(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t)

def nettoyer(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()

def date_de(txt):
    if not txt: return None
    try:
        d = parsedate_to_datetime(txt)
    except Exception:
        try: d = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        except Exception: return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def lire_flux(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        racine = ET.fromstring(r.read())
    items = []
    for it in racine.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"): continue
        def g(*noms):
            for n in noms:
                c = next((c for c in it if c.tag.split("}")[-1] == n), None)
                if c is not None: return c
            return None
        txt = lambda *noms: (g(*noms).text if g(*noms) is not None else "") or ""
        lien = g("link")
        lien = (lien.get("href") or lien.text) if lien is not None else ""
        src = g("source")
        items.append({
            "titre": nettoyer(txt("title")),
            "resume": nettoyer(txt("description", "summary")),
            "lien": (lien or "").strip(),
            "date": date_de(txt("pubDate", "published", "updated", "date")),
            "source_media": src.text if src is not None else "",
        })
    return items

def scorer(texte):
    t = norm(texte); score = 0; trouves = []
    for groupe, mots in CFG["mots_cles"].items():
        for m in mots:
            if norm(m) in t:
                score += POIDS.get(groupe, 1); trouves.append(m)
    return score, sorted(set(trouves))

def tache_google(q):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{CFG['jours']}d") + "&hl=fr&gl=FR&ceid=FR:fr"
    items = lire_flux(url)
    for i in items:
        # Google News : "Titre - Média"
        if not i["source_media"] and " - " in i["titre"]:
            i["source_media"] = i["titre"].rsplit(" - ", 1)[1]
        i["titre"] = re.sub(r"\s+-\s+[^-]+$", "", i["titre"]) if i["source_media"] else i["titre"]
        i["origine"] = f"Google News · « {q} »"; i["bonus"] = 1
    return items

def tache_rss(nom_url):
    nom, url = nom_url
    items = lire_flux(url)
    for i in items:
        i["source_media"] = i["source_media"] or nom; i["origine"] = f"RSS · {nom}"; i["bonus"] = 0
    return items

def main():
    limite = datetime.now(timezone.utc) - timedelta(days=CFG["jours"])
    taches = [(tache_google, q) for q in CFG["recherches_google_news"]] + [(tache_rss, kv) for kv in CFG["flux_rss"].items()]
    tous, erreurs = [], []
    with ThreadPoolExecutor(8) as ex:
        futurs = {ex.submit(f, a): a for f, a in taches}
        for fu, a in futurs.items():
            try: tous += fu.result()
            except Exception as e: erreurs.append(f"{a if isinstance(a, str) else a[0]} : {e.__class__.__name__}")

    retenus = []
    for i in tous:
        if not i["titre"] or (i["date"] and i["date"] < limite): continue
        s, mots = scorer(i["titre"] + " " + i["resume"])
        s += i["bonus"]
        if s < CFG["score_minimum"]: continue
        i["score"], i["mots"] = s, mots
        retenus.append(i)

    # dédoublonnage par titre similaire (on garde le meilleur score)
    retenus.sort(key=lambda x: -x["score"])
    uniques = []
    for i in retenus:
        n = norm(i["titre"])
        double = next((u for u in uniques if SequenceMatcher(None, n, norm(u["titre"])).ratio() > 0.72), None)
        if double:
            double.setdefault("autres", set()).add(i["source_media"])
            continue
        uniques.append(i)
    for u in uniques:  # plusieurs médias = sujet plus important
        u["score"] += len(u.get("autres", ()))

    vus = set(json.loads(DEJA_VUS.read_text())) if DEJA_VUS.exists() else set()
    for u in uniques: u["nouveau"] = norm(u["titre"]) not in vus
    DEJA_VUS.write_text(json.dumps(sorted(vus | {norm(u["titre"]) for u in uniques})[-5000:]))
    uniques.sort(key=lambda x: (not x["nouveau"], -x["score"], -(x["date"].timestamp() if x["date"] else 0)))

    maintenant = datetime.now()
    nouvelle_archive = not ARCHIVE.exists()
    with ARCHIVE.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if nouvelle_archive: w.writerow(["collecte", "date_article", "score", "media", "titre", "mots_cles", "lien"])
        for u in uniques:
            if u["nouveau"]:
                w.writerow([maintenant.strftime("%Y-%m-%d %H:%M"), u["date"].strftime("%Y-%m-%d %H:%M") if u["date"] else "",
                            u["score"], u["source_media"], u["titre"], ", ".join(u["mots"]), u["lien"]])

    fichier = SORTIE / ("index.html" if EN_LIGNE else f"veille_{maintenant:%Y-%m-%d_%Hh%M}.html")
    fichier.write_text(rendu(uniques, erreurs, maintenant), encoding="utf-8")
    (ICI / "derniere_veille.html").write_text(rendu(uniques, erreurs, maintenant), encoding="utf-8")
    nb_new = sum(u["nouveau"] for u in uniques)
    print(f"{len(tous)} articles lus · {len(uniques)} retenus · {nb_new} nouveaux")
    print(f"Rapport : {fichier}")
    if erreurs: print("Sources en erreur :", "; ".join(erreurs))
    telegram(uniques, maintenant)
    if "--ouvrir" in sys.argv: webbrowser.open(fichier.as_uri())

def tg(methode, **params):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN'].strip()}/{methode}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
        return json.loads(r.read())

def trouver_chats():
    """Lit chat_id.txt ; sinon récupère les personnes qui ont écrit au bot et les mémorise."""
    f = ICI / "chat_id.txt"
    ids = set(f.read_text().split()) if f.exists() else set()
    if os.environ.get("TELEGRAM_CHAT_ID"): ids |= set(os.environ["TELEGRAM_CHAT_ID"].split(","))
    try:
        for u in tg("getUpdates").get("result", []):
            m = u.get("message") or u.get("channel_post") or {}
            if m.get("chat", {}).get("id"): ids.add(str(m["chat"]["id"]))
    except Exception as e:
        print("getUpdates :", e)
    if ids: f.write_text("\n".join(sorted(ids)) + "\n")
    return sorted(ids)

def telegram(items, quand):
    if not os.environ.get("TELEGRAM_TOKEN"):
        return
    chats = trouver_chats()
    if not chats:
        print("Telegram : aucun chat trouvé. Envoie /start à ton bot puis relance."); return
    nouveaux = [u for u in items if u["nouveau"]][: int(os.environ.get("TELEGRAM_MAX", "15"))]
    e = html.escape
    lignes = [f"🗞 <b>Veille actu · {quand:%d/%m %H:%M}</b>",
              f"{sum(u['nouveau'] for u in items)} nouveaux articles · {len(items)} au total"]
    page = os.environ.get("PAGE_URL")
    if page: lignes.append(f'📄 <a href="{e(page)}">Rapport complet</a>')
    blocs = ["\n".join(lignes)]
    for u in nouveaux:
        pastille = "🔴" if u["score"] >= 9 else "🟡" if u["score"] >= 6 else "⚪"
        autres = len(u.get("autres", ()))
        blocs.append(f"{pastille} <b>{u['score']}</b> · {e(u['source_media'])}{f' (+{autres} médias)' if autres else ''}\n"
                     f'<a href="{e(u["lien"])}">{e(u["titre"])}</a>')
    if not nouveaux: blocs.append("Rien de nouveau depuis la dernière veille.")
    # découpe en messages < 4000 caractères
    messages, cur = [], ""
    for b in blocs:
        if len(cur) + len(b) + 2 > 3900: messages.append(cur); cur = ""
        cur += b + "\n\n"
    messages.append(cur)
    for chat in chats:
        for m in messages:
            try: tg("sendMessage", chat_id=chat, text=m, parse_mode="HTML", disable_web_page_preview="true")
            except Exception as ex: print("Telegram :", chat, ex)
    print(f"Telegram : envoyé à {len(chats)} chat(s)")

def rendu(items, erreurs, quand):
    e = html.escape
    cartes = []
    for u in items:
        niveau = "haut" if u["score"] >= 9 else "moyen" if u["score"] >= 6 else "bas"
        autres = ", ".join(sorted(u.get("autres", ())))
        cartes.append(f"""<article class="{niveau}" data-txt="{e(norm(u['titre'] + ' ' + u['source_media']))}">
<div class="meta">{'<b class="new">NOUVEAU</b>' if u['nouveau'] else ''}<span class="sc">{u['score']}</span>
<span>{e(u['source_media'])}</span><span>{u['date'].astimezone().strftime('%d/%m %H:%M') if u['date'] else ''}</span></div>
<h2><a href="{e(u['lien'])}" target="_blank" rel="noopener">{e(u['titre'])}</a></h2>
{f'<p>{e(u["resume"][:280])}</p>' if u['resume'] and norm(u['resume'])[:60] != norm(u['titre'])[:60] else ''}
<div class="tags">{''.join(f'<i>{e(m)}</i>' for m in u['mots'])}{f'<i class="aussi">aussi : {e(autres)}</i>' if autres else ''}</div>
</article>""")
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veille actu</title><style>
:root{{--bg:#0b1117;--card:#141c25;--tx:#e8edf2;--mut:#8a97a6;--red:#e2474b;--yel:#f2c94c;--grn:#3fb37f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,sans-serif}}
main{{max-width:860px;margin:auto;padding:16px}}h1{{font-size:22px;margin:8px 0}}.sub{{color:var(--mut);font-size:13px}}
input{{width:100%;padding:10px 12px;margin:14px 0;border-radius:8px;border:1px solid #2a3644;background:var(--card);color:var(--tx);font-size:15px}}
article{{background:var(--card);border-left:4px solid #3a4655;border-radius:8px;padding:12px 14px;margin:10px 0}}
article.haut{{border-color:var(--red)}}article.moyen{{border-color:var(--yel)}}
.meta{{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--mut);align-items:center}}
.sc{{background:#243040;color:var(--tx);padding:1px 7px;border-radius:10px;font-weight:700}}
.new{{background:var(--grn);color:#04130b;padding:1px 6px;border-radius:4px;font-size:11px}}
h2{{font-size:16px;margin:6px 0}}a{{color:var(--tx);text-decoration:none}}a:hover{{text-decoration:underline}}
p{{margin:4px 0;color:#c3ccd6;font-size:14px}}.tags i{{font-style:normal;font-size:11px;background:#1f2a36;color:var(--mut);padding:2px 6px;border-radius:4px;margin:4px 4px 0 0;display:inline-block}}
.tags i.aussi{{color:var(--yel)}}footer{{color:var(--mut);font-size:12px;margin:20px 0}}
</style></head><body><main>
<h1>Veille actu · {quand:%d/%m/%Y %H:%M}</h1>
<div class="sub">{len(items)} articles retenus · {sum(i['nouveau'] for i in items)} nouveaux · derniers {CFG['jours']} jours · bordure rouge = priorité haute</div>
<input id="q" placeholder="Filtrer (ex : tribunal, CNews, mosquée…)">
{''.join(cartes) or '<p>Aucun article trouvé.</p>'}
<footer>{('Sources en erreur : ' + e('; '.join(erreurs))) if erreurs else 'Toutes les sources ont répondu.'}</footer>
</main><script>
const n=s=>s.normalize('NFKD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase();
document.getElementById('q').oninput=e=>{{const v=n(e.target.value);document.querySelectorAll('article').forEach(a=>a.style.display=a.dataset.txt.includes(v)?'':'none')}};
</script></body></html>"""

if __name__ == "__main__":
    main()
