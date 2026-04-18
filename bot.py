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

LIGUES_IDS = [61, 39, 140, 135, 78, 2, 3]

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

def get_cotes_winamax(match_id, home, away):
    try:
        url = f"https://www.winamax.fr/paris-sportifs/sports/1/competitions"
        return None
    except:
        return None

def calculer_score_stats(fixture):
    try:
        stats = fixture.get("statistics", [])
        minute = fixture["fixture"]["status"]["elapsed"] or 0
        score_home = fixture["goals"]["home"] or 0
        score_away = fixture["goals"]["away"] or 0
        diff = abs(score_home - score_away)
        total_buts = score_home + score_away

        tirs_home = tirs_away = tirs_cadres_home = tirs_cadres_away = 0
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
                elif t == "Shots on Goal":
                    if is_home: tirs_cadres_home = val
                    else: tirs_cadres_away = val
                elif t == "Corner Kicks":
                    if is_home: corners_home = val
                    else: corners_away = val
                elif t == "expected_goals" or t == "Expected Goals":
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
    except Exception as e:
        return 0, {}

async def envoyer_alerte(fixture, score, infos):
    fixture_id = fixture["fixture"]["id"]
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    minute = infos.get("minute", 0)
    score_home = infos.get("score_home", 0)
    score_away = infos.get("score_away", 0)
    ligue = fixture["league"]["name"]
    pays = fixture["league"]["country"]

    if score >= 70:
        niveau = "Signal fort"
        emoji = "🟢"
    else:
        niveau = "Signal modéré"
        emoji = "🟡"

    prob = score / 100
    cote_mini = round(1 / prob, 2) if prob > 0.05 else 0

    message = (
        f"{emoji} *{niveau} — {score}/100*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⚽ *{home} vs {away}*\n"
        f"🕐 {minute}' — Score : {score_home}-{score_away}\n"
        f"🏆 {ligue} ({pays})\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 *Stats clés*\n"
        f"• Tirs totaux : {infos.get('tirs', 0)}\n"
        f"• xG total : {infos.get('xg', 0)}\n"
        f"• xG restant estimé : {infos.get('xg_restant', 0)}\n"
        f"• Corners : {infos.get('corners', 0)}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 *Cote minimum conseillée : {cote_mini}*\n"
        f"📌 Marché : Over {score_home + score_away + 0.5} buts\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_Analyse générée à {datetime.now().strftime('%H:%M:%S')}_"
    )

    await bot.send_message(
        chat_id=CHAT_ID,
        text=message,
        parse_mode=ParseMode.MARKDOWN
    )
    alertes_envoyees.add(fixture_id)

async def analyser_matchs():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Analyse en cours...")
    matchs = get_matchs_live()
    opportunites = 0

    for fixture in matchs:
        try:
            ligue_id = fixture["league"]["id"]
            if ligue_id not in LIGUES_IDS:
                continue

            minute = fixture["fixture"]["status"]["elapsed"]
            if not minute or minute < 60 or minute > 85:
                continue

            fixture_id = fixture["fixture"]["id"]
            if fixture_id in alertes_envoyees:
                continue

            score, infos = calculer_score_stats(fixture)

            if score >= 62:
                await envoyer_alerte(fixture, score, infos)
                opportunites += 1
                print(f"  ALERTE: {fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']} — Score {score}/100")

        except Exception as e:
            print(f"Erreur sur un match: {e}")
            continue

    print(f"  {len(matchs)} matchs analysés — {opportunites} alertes envoyées")

async def main():
    print("Bot Paris Live Football démarré")
    await bot.send_message(
        chat_id=CHAT_ID,
        text="✅ *Bot Paris Live Football démarré*\nJe surveille tous les matchs live et t'enverrai des alertes dès qu'une opportunité se présente entre la 60e et 85e minute.",
        parse_mode=ParseMode.MARKDOWN
    )
    scheduler.add_job(analyser_matchs, "interval", minutes=3)
    scheduler.start()
    print("Scheduler démarré — analyse toutes les 3 minutes")
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
