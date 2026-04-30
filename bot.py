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
snapshots_match = {}
historique_resultats = {}

TIMEZONE_FR = pytz.timezone("Europe/Paris")

LIGUES_AUTORISEES = [
    61, 39, 40, 140, 141, 135, 78, 79,
    144, 71, 172, 292, 210, 119, 179,
    103, 88, 94, 113, 203
]

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

def extraire_stats(fixture):
    try:
        stats = fixture.get("statistics", [])
        minute = fixture["fixture"]["status"]["elapsed"] or 0
        score_home = fixture["goals"]["home"] or 0
        score_away = fixture["goals"]["away"] or 0

        corners_home = corners_away = 0
        xg_home = xg_away = 0.0
        tirs_surface_home = tirs_surface_away = 0

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
                if t == "Corner Kicks":
                    if is_home: corners_home = val
                    else: corners_away = val
                elif t in ["expected_goals", "Expected Goals"]:
                    if is_home: xg_home = val
                    else: xg_away = val
                elif t == "Shots insidebox":
                    if is_home: tirs_surface_home = val
                    else: tirs_surface_away = val

        return {
            "minute": minute,
            "score_home": score_home,
            "score_away": score_away,
            "xg_home": round(xg_home, 2),
            "xg_away": round(xg_away, 2),
            "xg_total": round(xg_home + xg_away, 2),
            "corners_total": int(corners_home + corners_away),
            "tirs_surface_total": int(tirs_surface_home + tirs_surface_away)
        }
    except:
        return None

def calculer_intensite_recente(fixture_id, stats_actuelles):
    if fixture_id not in snapshots_match:
        snapshots_match[fixture_id] = stats_actuelles
        return None

    snapshot = snapshots_match[fixture_id]
    delta = {
        "delta_xg_home": round(stats_actuelles["xg_home"] - snapshot["xg_home"], 2),
        "delta_xg_away": round(stats_actuelles["xg_away"] - snapshot["xg_away"], 2),
        "delta_xg_total": round(stats_actuelles["xg_total"] - snapshot["xg_total"], 2),
        "delta_corners": stats_actuelles["corners_total"] - snapshot["corners_total"],
        "delta_tirs_surface": stats_actuelles["tirs_surface_total"] - snapshot["tirs_surface_total"],
        "delta_minute": stats_actuelles["minute"] - snapshot["minute"]
    }

    snapshots_match[fixture_id] = stats_actuelles
    return delta

def detecter_alerte(stats, intensite, home, away):
    if not intensite or intensite["delta_minute"] < 1:
        return None

    delta_xg_max = max(intensite["delta_xg_home"], intensite["delta_xg_away"])
    delta_tirs_surface = intensite["delta_tirs_surface"]
    delta_corners = intensite["delta_corners"]

    # Regle 1 — xG explosif
    if delta_xg_max >= 0.15:
        equipe = home if intensite["delta_xg_home"] >= 0.15 else away
        return f"{equipe} pousse fort (xG +{delta_xg_max} en {intensite['delta_minute']}min)"

    # Regle 2 — Pression cumulee
    if delta_tirs_surface >= 2:
        return f"{delta_tirs_surface} tirs en surface en {intensite['delta_minute']}min"

    # Regle 3 — Match qui s'emballe
    if delta_corners >= 3:
        return f"{delta_corners} corners en {intensite['delta_minute']}min"

    return None

async def envoyer_alerte(fixture, stats, intensite, situation, cote_actuelle):
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
        f"📊 Intensite des {intensite['delta_minute']} dernieres minutes\n"
        f"• xG genere : {home} +{intensite['delta_xg_home']} | {away} +{intensite['delta_xg_away']}\n"
        f"• Tirs en surface : +{intensite['delta_tirs_surface']}\n"
        f"• Nouveaux corners : {intensite['delta_corners']}\n"
        f"———————————————\n"
        f"💰 Marche conseille : {marche}\n"
        f"📈 Cote actuelle : {cote_info}\n"
        f"———————————————\n"
        f"⏰ {heure_fr}"
    )

    await bot.send_message(chat_id=CHAT_ID, text=message)

    historique_resultats[fixture_id] = {
        "home": home,
        "away": away,
        "ligue": ligue,
        "buts_au_moment": score_home + score_away
    }

    alertes_envoyees.add(fixture_id)
    print(f"  ALERTE: {home} vs {away} — {situation}")

async def verifier_resultats():
    a_supprimer = []
    for fixture_id, data in list(historique_resultats.items()):
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
        if fixture_id in historique_resultats:
            del historique_resultats[fixture_id]

async def analyser_matchs():
    if not est_heure_matchs():
        print(f"[{heure_france().strftime('%H:%M')}] Hors horaire FR — pause")
        return

    print(f"[{heure_france().strftime('%H:%M:%S')}] Analyse en cours...")
    matchs = get_matchs_live()
    matchs_filtres = [m for m in matchs if m["league"]["id"] in LIGUES_AUTORISEES]
    opportunites = 0

    for fixture in matchs_filtres:
        try:
            minute = fixture["fixture"]["status"]["elapsed"]
            if not minute or minute < 75 or minute > 92:
                continue

            fixture_id = fixture["fixture"]["id"]
            if fixture_id in alertes_envoyees:
                continue

            home = fixture["teams"]["home"]["name"]
            away = fixture["teams"]["away"]["name"]

            stats = extraire_stats(fixture)
            if not stats:
                continue

            intensite = calculer_intensite_recente(fixture_id, stats)
            if not intensite:
                continue

            situation = detecter_alerte(stats, intensite, home, away)
            if not situation:
                continue

            cote_actuelle = get_cotes_live(fixture_id)
            await envoyer_alerte(fixture, stats, intensite, situation, cote_actuelle)
            opportunites += 1

        except Exception as e:
            print(f"Erreur: {e}")
            continue

    print(f"  {len(matchs)} matchs live ({len(matchs_filtres)} dans nos ligues) — {opportunites} alertes")
    await verifier_resultats()

async def main():
    print("Bot Paris Live Football v16 demarre")
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "✅ Bot Paris Live Football v16\n"
                "———————————————\n"
                "🌍 20 championnats selectionnes\n"
                "⏱ Fenetre : 75e — 92e minute\n"
                "⚡ 3 regles simples :\n"
                "• xG +0.15 en 2 min\n"
                "• 2+ tirs en surface en 2 min\n"
                "• 3+ corners en 2 min\n"
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
    print("Scheduler demarre — 3 regles simples actives")

    while True:
        await asyncio.sleep(60)
        print(f"[{heure_france().strftime('%H:%M:%S')}] Bot actif...")

if __name__ == "__main__":
    asyncio.run(main())
