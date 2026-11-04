#!/usr/bin/env python3
"""Génère un script vidéo (format vertical, style « récit d'actu ») à partir d'un article
et l'envoie sur Telegram. Lancé par GitHub Actions quand on appuie sur un bouton 🎬 du bot.
Entrée : variable PAYLOAD (JSON) = {"chat": ..., "id": "..."} ou {"chat": ..., "url": "..."} ou {"chat": ..., "texte": "..."}"""
import json, os, sys, urllib.parse, urllib.request
from pathlib import Path

import anthropic

ICI = Path(__file__).resolve().parent
MODELE = os.environ.get("MODELE_SCRIPT", "claude-opus-5-5")

SYSTEME = """Tu es le rédacteur en chef d'un média vidéo vertical (TikTok / Reels / Shorts) qui couvre
l'actualité qui touche la communauté maghrébine et musulmane de France : racisme, discriminations,
justice, médias, discours politiques. Tu écris des scripts de 60 à 110 secondes.

STYLE (à respecter strictement)
- Accroche immédiate (0-3 s) : le fait le plus choquant, en une phrase courte. Pas d'introduction.
- Phrases très courtes. Une idée par plan. Questions rhétoriques pour enchaîner (« Et les chaînes ? »).
- Les citations exactes sont l'arme principale : on montre les mots, on ne les résume pas.
- Toujours : qui, où, quand (dates précises), puis la preuve, puis une source officielle
  (procureur, tribunal, autorité, avocat), puis la mise en perspective (autres affaires, chronologie),
  puis la conclusion et l'appel à l'action (signer, partager, signaler - Pharos, Arcom).
- Le « nous » de la communauté n'apparaît qu'à la fin, après les faits.

RIGUEUR (non négociable)
- Utilise la recherche web pour vérifier les faits et trouver au moins 2 sources fiables.
- N'invente JAMAIS une citation, un chiffre, une date ou un nom. Une citation entre guillemets
  doit venir mot pour mot d'une source. Si une info n'est pas vérifiable, ne l'utilise pas.
- Présomption d'innocence : « mis en cause », « suspect », « présumé » tant qu'il n'y a pas de condamnation.
- Si deux faits sont liés par toi (chronologie), formule-le comme un rapprochement, pas comme une causalité prouvée.
- Pas d'appel à la haine, pas de généralisation sur un groupe ; on vise des actes, des propos, des institutions.
- Ne donne pas l'adresse ou des infos privées de particuliers ; floute les anonymes.

FORMAT DE SORTIE (texte brut, pas de markdown, en français)
TITRE MINIATURE : 2 lignes max, en majuscules (ligne 1 blanche / ligne 2 rouge)
DURÉE ESTIMÉE : xx s
FIL D'ARIANE : MOT · MOT · MOT

Puis les plans numérotés, chacun sous cette forme :
PLAN 1 (0-3 s)
VISUEL : ce qu'on montre (photo, extrait TV, capture d'article, carte, fond sombre…)
TEXTE ÉCRAN : les blocs empilés, avec la couleur entre crochets : [BLANC] … / [ROUGE] … / [JAUNE] date ou chiffre / [VERT] appel à l'action / [TAMPON] mot-choc incliné
VOIX OFF : la phrase exacte (c'est aussi le sous-titre)

Termine par :
CTA FINAL : …
SOURCES : liste (média - date - lien)
À VÉRIFIER : ce qui reste incertain (ou « rien »)"""


def tg(methode, **params):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN'].strip()}/{methode}"
    req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def envoyer(chat, texte):
    for i in range(0, len(texte), 3900):
        tg("sendMessage", chat_id=chat, text=texte[i:i + 3900], disable_web_page_preview="true")


def demande(payload):
    if payload.get("id"):
        articles = json.loads((ICI / "articles.json").read_text(encoding="utf-8"))
        a = articles.get(payload["id"])
        if not a:
            raise LookupError("Article introuvable (trop ancien ?). Envoie-moi directement le lien.")
        return (f"Écris le script vidéo sur cette actualité.\n\nTitre : {a['titre']}\nMédia : {a['media']}\n"
                f"Date : {a['date']}\nLien (peut être une redirection Google News) : {a['lien']}\n"
                f"Résumé : {a['resume']}\n\nCherche l'article original et d'autres sources avant d'écrire."), a["titre"]
    if payload.get("url"):
        return (f"Écris le script vidéo sur l'actualité de ce lien : {payload['url']}\n"
                "Lis la page, puis cherche d'autres sources pour vérifier avant d'écrire."), payload["url"]
    return (f"Écris le script vidéo sur ce sujet : {payload['texte']}\n"
            "Cherche les faits et les sources avant d'écrire."), payload["texte"][:80]


def generer(question):
    client = anthropic.Anthropic()
    outils = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 6},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
    ]
    messages = [{"role": "user", "content": question}]
    for _ in range(6):
        rep = client.beta.messages.create(
            model=MODELE,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},  # si le modèle refuse, l'API relance sur le modèle de secours
            system=SYSTEME,
            output_config={"effort": "medium"},
            tools=outils,
            messages=messages,
        )
        if rep.stop_reason == "pause_turn":  # recherche web longue : on laisse le serveur continuer
            messages.append({"role": "assistant", "content": rep.content})
            continue
        break
    if rep.stop_reason == "refusal":
        raise RuntimeError("Le modèle a refusé d'écrire ce script.")
    texte = "\n".join(b.text for b in rep.content if b.type == "text").strip()
    if not texte:
        raise RuntimeError(f"Réponse vide (stop_reason={rep.stop_reason}).")
    return texte


def main():
    payload = json.loads(os.environ.get("PAYLOAD") or "{}")
    chat = str(payload.get("chat", ""))
    autorises = set((ICI / "chat_id.txt").read_text().split()) if (ICI / "chat_id.txt").exists() else set()
    if not chat or chat not in autorises:
        print("Chat non autorisé :", chat); sys.exit(1)
    try:
        question, sujet = demande(payload)
        print("Sujet :", sujet)
        script = generer(question)
        envoyer(chat, f"🎬 SCRIPT - {sujet}\n\n{script}")
        print("Script envoyé.")
    except anthropic.AuthenticationError:
        envoyer(chat, "❌ Clé ANTHROPIC_API_KEY invalide. Vérifie le secret dans GitHub.")
        raise
    except anthropic.APIStatusError as e:
        envoyer(chat, f"❌ Erreur de l'API Claude ({e.status_code}) : {e.message[:300]}")
        raise
    except Exception as e:
        envoyer(chat, f"❌ {e}")
        raise


if __name__ == "__main__":
    main()
