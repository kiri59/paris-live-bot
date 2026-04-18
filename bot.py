import os
import asyncio
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
scheduler = AsyncIOScheduler()
alertes_envoyees = set()
historique_cotes = {}

def get_matchs_live():
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"live": "all"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json()
        return data.get("response", [])
    except:
        return []

def get_cotes_live(fixture_id):
    url = "https://v3.football.api-sports.io/odds/live"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"fixture": fixture_id}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json()
        resultats = data.get("response", [])
        if not resultats:
            return None
        for bookmaker in resultats[0].get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                if "Over/Under" in bet.get("name", ""):
                    for val in bet.get("values", []):
                        if val.get("value") == "Over 2.5":
                            return float(val.get("odd", 0))
        return None
    except:
        return None

def analyser_mouvement_cotes(fixture_id, cote_actuelle):
    if cote_actuelle is None:
        return "neutre", 0
    historique = historique_cotes.get(fixture_id, [])
    historique.append({"cote": cote_actuelle, "time": datetime.now()})
    if len(historique) > 10:
        historique = historique[-10:]
    historique_cotes[fixture_id] = historique
    if len(historique) < 2:
        return "neutre", 0
    premiere_cote = historique[0]["cote"]
    variation = ((cote_actuelle - premiere_cote) / premiere_cote) * 100
    if variation <= -8:
        return "fort", round(abs(variation), 1)
    elif variation <= -4:
        return "modere", round(abs(variation), 1)
    elif variation >= 8:
        return "contre", round(abs(variation), 1)
    else:
        return "neutre", round(abs(variation), 1)

def calculer_score_stats(fixture):
    try:
        stats = fixture.get("statistics", [])
        minute = fixture["fixture"]["status"]["elapsed"] or 0
        score_home = fixture["goals"]["home"] or 0
        score_away = fixture["goals"]["away"] or 0
        diff = abs(score_home - score_away)
        total_buts = score_home + score_away

        tirs_home = tirs_away = 0
        corners_home = corners_away = 0
        xg_home = xg_away = 0.0

        for team_stats in stats:
            is_home = team_stats["team"]["id"] == fixture["teams"]["home"]["id"]
            for s in team_stats["statistics"]:
                val = s["value"]
                if val is None:
                    val = 0
                try:
                    val = float(str(val).replace("%", ""))
                except:
                    val = 0
                t = s["type"]
                if t == "Total Shots":
                    if is_home: tirs_home = val
                    else: tirs_away = val
                elif t == "Corner Kicks":
                    if is_home: corners_home = val
                    else: corners_away = val
                elif t in ["expected_goals", "Expected Goals"]:
                    if is_home: xg_home = val
                    else: xg_away = val

        score = 0
        minutes_restantes = max(90 - minute, 1)
        tirs_totaux = tirs_home + tirs_away
        rythme = (tirs_totaux / max(minute, 1)) * 90
        score += min(rythme / 25, 1) * 25

        xg_total = xg_home + xg_away
        xg_restant = xg_total * (minutes_restantes / max(minute, 1))
        score += min(xg_restant / 0.8, 1) * 25

        corners_total = corners_home + corners_away
        score += min(corners_total / 12, 1) * 20

        ctx = 0
        if diff == 0: ctx += 0.5
        elif diff == 1: ctx += 0.35
        if total_buts <= 1: ctx += 0.2
        ctx = min(ctx, 1)
        score += ctx * 30

        return round(min(score, 100)), {
            "tirs": int(tirs_totaux),
            "xg": round(xg_total, 2),
            "xg_restant": round(xg_restant, 2),
            "corners": int(corners_total),
            "minute": minute,
            "score_home": score_home,
            "score_away": score_away,
            "diff": diff
        }
    except:
        return 0, {}

def calculer_score_final(score_stats, mouvement):
    bonus = 0
    if mouvement == "fort": bonus = 15
    elif mouvement == "modere": bonus = 8
    elif mouvement == "contre": bonus = -15
    return min(round(score_stats + bonus), 100)

async def envoyer_alerte(fixture, score_stats, score_final, infos, mouvement, variation, cote_actuelle):
    fixture_id = fixture["fixture"]["id"]
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    minute = infos.get("minute", 0)
    score_home = infos.get("score_home", 0)
    score_away = infos.get("score_away", 0)
    ligue = fixture["league"]["name"]
    pays = fixture["league"]["country"]

    if score_final >= 75:
        niveau = "Signal très fort"
        emoji = "🟢"
    else:
        niveau = "Signal modéré"
        emoji = "🟡"

    prob = score_final / 100
    cote_mini = round(1 / prob, 2) if prob > 0.05 else 0

    if mouvement == "fort":
        money_line = f"📉 Chute de cote -{variation}% — argent massif sur over"
        money_emoji = "🔥"
    elif mouvement == "modere":
        money_line = f"📉 Chute de cote -{variation}% — signal money flow"
        money_emoji = "📊"
    elif mouvement == "contre":
        money_line = f"📈 Hausse de cote +{variation}% — argent contre"
        money_emoji = "⚠️"
    else:
        money_line = "➡️ Cote stable — pas de signal money flow"
        money_emoji = "➡️"

    cote_info = f"{cote_actuelle}" if cote_actuelle else "N/A"
    marche = f"Over {score_home + score_away + 0.5} buts"

    message = (
        f"{emoji} *{niveau} — {score_final}/100*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⚽ *{home} vs {away}*\n"
        f"🕐 {minute}' — Score : {score_home}-{score_away}\n"
        f"🏆 {ligue} \\({pays}\\)\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 *Stats football : {score_stats}/100*\n"
        f"• Tirs totaux : {infos.get('tirs', 0)}\n"
        f"• xG total : {infos.get('xg', 0)}\n"
        f"• xG restant estimé : {infos.get('xg_restant', 0)}\n"
        f"• Corners : {infos.get('corners', 0)}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{money_emoji} *Mouvement de cotes*\n"
        f"{money_line}\n"
        f"• Cote over actuelle : {cote_info}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 *Cote minimum conseillée : {cote_mini}*\n"
        f"📌 Marché : {marche}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_Analyse générée à {datetime.now().strftime('%H:%M:%S')}_"
    )

    await bot.send_message(
        chat_id=CHAT_ID,
        text=message,
        parse_mode=ParseMode.MARKDOWN
    )
    alertes_envoyees.add(fixture_id)
    print(f"  ALERTE envoyée: {home} vs {away} — {score_final}/100")

async def analyser_matchs():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Analyse en cours...")
    matchs = get_matchs_live()
    opportunites = 0

    for fixture in matchs:
        try:
            minute = fixture["fixture"]["status"]["elapsed"]
            if not minute or minute < 60 or minute > 85:
                continue

            fixture_id = fixture["fixture"]["id"]
            if fixture_id in alertes_envoyees:
                continue

            score_stats, infos = calculer_score_stats(fixture)
            if score_stats < 45:
                continue

            cote_actuelle = get_cotes_live(fixture_id)
            mouvement, variation = analyser_mouvement_cotes(fixture_id, cote_actuelle)
            score_final = calculer_score_final(score_stats, mouvement)

            if score_final >= 62:
                await envoyer_alerte(fixture, score_stats, score_final, infos, mouvement, variation, cote_actuelle)
                opportunites += 1

        except Exception as e:
            print(f"Erreur: {e}")
            continue

    print(f"  {len(matchs)} matchs analysés — {opportunites} alertes envoyées")

async def main():
    print("Bot Paris Live Football v3 démarré — Tous championnats")
    await bot.send_message(
        chat_id=CHAT_ID,
        text="✅ *Bot Paris Live Football v3 démarré*\n🌍 Surveillance de TOUS les championnats disponibles\n📊 Signal double activé : stats \\+ mouvement de cotes\n🕐 Analyse toutes les 3 minutes entre la 60e et 85e minute",
        parse_mode=ParseMode.MARKDOWN
    )
    scheduler.add_job(analyser_matchs, "interval", minutes=3)
    scheduler.start()
    print("Scheduler démarré")
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
