import os
import asyncio
import requests
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
scheduler = AsyncIOScheduler()
alertes_envoyees = set()
historique_cotes = {}

TIMEZONE_FR = pytz.timezone("Europe/Paris")

def heure_france():
    return datetime.now(TIMEZONE_FR)

def est_heure_matchs():
    heure = heure_france().hour
    return 12 <= heure <= 23

def get_matchs_live():
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"live": "all"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        return r.json().get("response", [])
    except:
        return []

def get_match_termine(fixture_id):
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"id": fixture_id}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json().get("response", [])
        if not data:
            return None
        fixture = data[0]
        status = fixture["fixture"]["status"]["short"]
        if status in ["FT", "AET", "PEN"]:
            return {
                "score_home": fixture["goals"]["home"] or 0,
                "score_away": fixture["goals"]["away"] or 0,
            }
        return None
    except:
        return None

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

def analyser_stats(fixture):
    try:
        stats = fixture.get("statistics", [])
        minute = fixture["fixture"]["status"]["elapsed"] or 0
        score_home = fixture["goals"]["home"] or 0
        score_away = fixture["goals"]["away"] or 0
        diff = score_home - score_away

        tirs_home = tirs_away = 0
        corners_home = corners_away = 0
        xg_home = xg_away = 0.0
        poss_home = poss_away = 50.0

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
                elif t == "Ball Possession":
                    if is_home: poss_home = val
                    else: poss_away = val

        return {
            "minute": minute,
            "score_home": score_home,
            "score_away": score_away,
            "diff": diff,
            "tirs_home": int(tirs_home),
            "tirs_away": int(tirs_away),
            "corners_home": int(corners_home),
            "corners_away": int(corners_away),
            "xg_home": round(xg_home, 2),
            "xg_away": round(xg_away, 2),
            "poss_home": round(poss_home),
            "poss_away": round(poss_away),
            "tirs_total": int(tirs_home + tirs_away),
            "corners_total": int(corners_home + corners_away),
            "xg_total": round(xg_home + xg_away, 2)
        }
    except:
        return None

def detecter_situation(stats, home, away):
    if not stats:
        return None, None

    minute = stats["minute"]
    diff = stats["diff"]
    score_home = stats["score_home"]
    score_away = stats["score_away"]
    tirs_total = stats["tirs_total"]
    corners_total = stats["corners_total"]
    xg_total = stats["xg_total"]
    poss_home = stats["poss_home"]
    poss_away = stats["poss_away"]

    signaux = []
    situation = None

    # Equipe qui perd et qui pousse
    if diff < 0 and poss_home >= 60 and tirs_total >= 15:
        signaux.append(f"{home} perd et domine le jeu ({poss_home}% possession, {stats['tirs_home']} tirs)")
        situation = f"{home} pousse pour revenir au score"
    elif diff > 0 and poss_away >= 60 and tirs_total >= 15:
        signaux.append(f"{away} perd et domine le jeu ({poss_away}% possession, {stats['tirs_away']} tirs)")
        situation = f"{away} pousse pour revenir au score"

    # Match nul avec forte pression
    elif diff == 0 and tirs_total >= 18 and corners_total >= 8:
        signaux.append(f"Match nul tendu avec forte pression ({tirs_total} tirs, {corners_total} corners)")
        situation = "Match ouvert — les deux equipes cherchent le but"

    # xG eleve en fin de match
    elif xg_total >= 2.5 and tirs_total >= 20:
        signaux.append(f"Pression offensive intense (xG {xg_total}, {tirs_total} tirs)")
        situation = "Match a haute intensite offensive"

    # Equipe qui gagne et continue de pousser
    elif abs(diff) == 1 and tirs_total >= 16 and corners_total >= 7:
        if diff > 0:
            signaux.append(f"{home} mene et continue de dominer ({poss_home}% possession)")
            situation = f"{home} cherche a tuer le match"
        else:
            signaux.append(f"{away} mene et continue de dominer ({poss_away}% possession)")
            situation = f"{away} cherche a tuer le match"

    if not signaux:
        return None, None

    return situation, signaux

async def envoyer_alerte(fixture, stats, situation, signaux, cote_actuelle):
    fixture_id = fixture["fixture"]["id"]
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    minute = stats["minute"]
    score_home = stats["score_home"]
    score_away = stats["score_away"]
    ligue = fixture["league"]["name"]
    pays = fixture["league"]["country"]

    cote_info = f"{cote_actuelle}" if cote_actuelle else "N/A"
    marche = f"Over {score_home + score_away + 0.5} buts"
    heure_fr = heure_france().strftime('%H:%M:%S')

    message = (
        f"⚡ VALUE BET — 1 BUT A VENIR\n"
        f"———————————————\n"
        f"⚽ {home} vs {away}\n"
        f"🕐 {minute}' — Score : {score_home}-{score_away}\n"
        f"🏆 {ligue} ({pays})\n"
        f"———————————————\n"
        f"📋 Situation : {situation}\n"
        f"———————————————\n"
        f"📊 Stats live\n"
        f"• Tirs : {home} {stats['tirs_home']} — {stats['tirs_away']} {away}\n"
        f"• Corners : {stats['corners_home']} — {stats['corners_away']}\n"
        f"• xG : {stats['xg_home']} — {stats['xg_away']}\n"
        f"• Possession : {stats['poss_home']}% — {stats['poss_away']}%\n"
        f"———————————————\n"
        f"💰 Marche conseille : {marche}\n"
        f"📈 Cote actuelle : {cote_info}\n"
        f"———————————————\n"
        f"⏰ {heure_fr}"
    )

    await bot.send_message(chat_id=CHAT_ID, text=message)
    alertes_envoyees.add(fixture_id)
    print(f"  VALUE BET: {home} vs {away} — {situation}")

async def verifier_resultats():
    a_supprimer = []
    for fixture_id, data in list(historique_cotes.items()):
        if "buts_au_moment" not in data:
            continue
        try:
            resultat = get_match_termine(fixture_id)
            if resultat is None:
                continue
            total_final = resultat["score_home"] + resultat["score_away"]
            buts_apres = total_final - data["buts_au_moment"]
            gagnant = buts_apres > 0

            if gagnant:
                emoji_r = "✅"
                verdict = "GAGNANT"
                detail = f"But marque apres l'alerte — score final {resultat['score_home']}-{resultat['score_away']}"
            else:
                emoji_r = "❌"
                verdict = "PERDANT"
                detail = f"Aucun but — score final {resultat['score_home']}-{resultat['score_away']}"

            message = (
                f"{emoji_r} RESULTAT — {verdict}\n"
                f"———————————————\n"
                f"⚽ {data['home']} vs {data['away']}\n"
                f"🏆 {data['ligue']}\n"
                f"———————————————\n"
                f"📊 {detail}\n"
                f"———————————————\n"
                f"Match termine"
            )
            await bot.send_message(chat_id=CHAT_ID, text=message)
            a_supprimer.append(fixture_id)
            print(f"  RESULTAT: {data['home']} vs {data['away']} — {verdict}")
        except Exception as e:
            print(f"Erreur resultat: {e}")
            continue
    for fixture_id in a_supprimer:
        if fixture_id in historique_cotes:
            del historique_cotes[fixture_id]

async def analyser_matchs():
    if not est_heure_matchs():
        print(f"[{heure_france().strftime('%H:%M')}] Hors horaire FR — pause")
        return

    print(f"[{heure_france().strftime('%H:%M:%S')}] Analyse en cours...")
    matchs = get_matchs_live()
    opportunites = 0

    for fixture in matchs:
        try:
            minute = fixture["fixture"]["status"]["elapsed"]
            if not minute or minute < 80 or minute > 92:
                continue

            fixture_id = fixture["fixture"]["id"]
            if fixture_id in alertes_envoyees:
                continue

            home = fixture["teams"]["home"]["name"]
            away = fixture["teams"]["away"]["name"]

            stats = analyser_stats(fixture)
            if not stats:
                continue

            situation, signaux = detecter_situation(stats, home, away)
            if not situation:
                continue

            cote_actuelle = get_cotes_live(fixture_id)

            historique_cotes[fixture_id] = {
                "home": home,
                "away": away,
                "ligue": fixture["league"]["name"],
                "buts_au_moment": stats["score_home"] + stats["score_away"]
            }

            await envoyer_alerte(fixture, stats, situation, signaux, cote_actuelle)
            opportunites += 1

        except Exception as e:
            print(f"Erreur: {e}")
            continue

    print(f"  {len(matchs)} matchs analyses — {opportunites} value bets detectes")
    await verifier_resultats()

async def main():
    print("Bot Paris Live Football v12 demarre")
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "✅ Bot Paris Live Football v12\n"
                "———————————————\n"
                "🌍 Tous les championnats\n"
                "⏱ Fenetre : 80e — 92e minute\n"
                "⚡ Value bets — 1 but a venir\n"
                "📊 Analyse par situation tactique\n"
                "🕐 Actif 12h-23h heure francaise\n"
                "🏆 Resultats automatiques\n"
                "———————————————\n"
                "En surveillance..."
            )
        )
    except Exception as e:
        print(f"Erreur demarrage: {e} — bot continue")

    scheduler.add_job(analyser_matchs, "interval", minutes=2)
    scheduler.start()
    print("Scheduler demarre — heure FR active")

    while True:
        await asyncio.sleep(60)
        print(f"[{heure_france().strftime('%H:%M:%S')}] Bot actif...")

if __name__ == "__main__":
    asyncio.run(main())
