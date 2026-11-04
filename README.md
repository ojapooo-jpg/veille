# Veille actu

Collecte automatique d'articles (Google News + flux RSS), filtrés par mots-clés et classés par score.
Tourne deux fois par jour sur GitHub Actions, envoie les nouveaux articles sur Telegram
et publie le rapport complet sur GitHub Pages.

- Modifier les mots-clés, recherches et journaux : `config.json`
- Lancer à la main : onglet **Actions** → *Veille actu* → **Run workflow**
- Archive de tous les articles : `archive.csv`

## Bot « script vidéo »

- Bouton 🎬 sous chaque info, lien envoyé au bot, ou `/script sujet` → script vidéo prêt à tourner.
- `worker/` : petit bot Cloudflare qui reçoit les clics et lance le workflow **Script vidéo**.
- `script.py` : recherche les sources et écrit le script avec Claude, puis l'envoie sur Telegram.
- Secrets GitHub nécessaires : `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`, `CLOUDFLARE_API_TOKEN`,
  `CLOUDFLARE_ACCOUNT_ID`, `GH_DISPATCH_TOKEN`. Puis lancer **Actions → Déployer le bot**.
