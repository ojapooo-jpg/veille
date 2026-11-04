// Bot Telegram « Veille actu » - Cloudflare Worker (aucune dépendance).
// Reçoit les clics sur les boutons 🎬 et les liens envoyés, puis demande à GitHub Actions
// d'écrire le script (le travail long se fait dans GitHub, pas ici).

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("Veille bot OK");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json();
    ctx.waitUntil(traiter(update, env).catch((e) => console.log("Erreur", e)));
    return new Response("ok");
  },
};

async function tg(env, methode, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${methode}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  return r.json();
}

async function lancerScript(env, payload) {
  const r = await fetch(`https://api.github.com/repos/${env.REPO}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "veille-bot",
      "content-type": "application/json",
    },
    body: JSON.stringify({ event_type: "script", client_payload: payload }),
  });
  return r.status === 204 ? null : `GitHub ${r.status} : ${(await r.text()).slice(0, 200)}`;
}

async function traiter(update, env) {
  const autorises = (env.ALLOWED_CHATS || "").split(",").map((s) => s.trim()).filter(Boolean);

  // Clic sur un bouton 🎬
  if (update.callback_query) {
    const cq = update.callback_query;
    const chat = String(cq.message?.chat?.id ?? cq.from.id);
    if (!autorises.includes(chat)) {
      await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "Non autorisé" });
      return;
    }
    const [type, id] = (cq.data || "").split(":");
    if (type !== "s" || !id) return;
    await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "⏳ Script en cours…" });
    const err = await lancerScript(env, { chat, id });
    await tg(env, "sendMessage", {
      chat_id: chat,
      text: err ? `❌ Impossible de lancer le script. ${err}` : "⏳ C'est parti ! Le script arrive dans 1 à 3 minutes.",
    });
    return;
  }

  // Message texte
  const msg = update.message;
  if (!msg?.text) return;
  const chat = String(msg.chat.id);
  if (!autorises.includes(chat)) {
    await tg(env, "sendMessage", { chat_id: chat, text: `Bot privé. Ton identifiant : ${chat}` });
    return;
  }
  const texte = msg.text.trim();
  if (texte.startsWith("/start") || texte.startsWith("/aide")) {
    await tg(env, "sendMessage", {
      chat_id: chat,
      text:
        "👋 Veille actu\n\n" +
        "• Chaque matin et chaque soir je t'envoie les nouvelles infos.\n" +
        "• Appuie sur 🎬 + numéro pour recevoir le script vidéo.\n" +
        "• Envoie-moi un lien d'article → je fais le script.\n" +
        "• /script + un sujet (ex : /script mosquée de Lodève) → je cherche et je fais le script.",
    });
    return;
  }
  let payload = null;
  const lien = texte.match(/https?:\/\/\S+/);
  if (lien) payload = { chat, url: lien[0] };
  else if (texte.startsWith("/script ")) payload = { chat, texte: texte.slice(8).trim() };
  if (!payload) {
    await tg(env, "sendMessage", { chat_id: chat, text: "Envoie-moi un lien, ou /script + un sujet. /aide pour l'aide." });
    return;
  }
  const err = await lancerScript(env, payload);
  await tg(env, "sendMessage", {
    chat_id: chat,
    text: err ? `❌ Impossible de lancer le script. ${err}` : "⏳ C'est parti ! Le script arrive dans 1 à 3 minutes.",
  });
}
