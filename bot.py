import os
import asyncio
import requests
from datetime import datetime, timedelta
import pytz
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash-exp')

TIMEZONE_FR = pytz.timezone("Europe/Paris")

LIGUES_PRINCIPALES = [61, 39, 140, 135, 78, 71]

def heure_france():
    return datetime.now(TIMEZONE_FR)

def get_matchs_a_venir(jours=7):
    """Récupère tous les matchs à venir dans les X prochains jours"""
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    date_debut = heure_france().strftime('%Y-%m-%d')
    date_fin = (heure_france() + timedelta(days=jours)).strftime('%Y-%m-%d')
    
    all_matches = []
    for league_id in LIGUES_PRINCIPALES:
        params = {
            "league": league_id,
            "season": 2025,
            "from": date_debut,
            "to": date_fin
        }
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            data = r.json().get("response", [])
            all_matches.extend(data)
        except:
            continue
    
    return all_matches

def chercher_match(equipe1, equipe2):
    """Trouve un match entre deux équipes"""
    matchs = get_matchs_a_venir()
    
    equipe1_lower = equipe1.lower()
    equipe2_lower = equipe2.lower()
    
    for match in matchs:
        home = match["teams"]["home"]["name"].lower()
        away = match["teams"]["away"]["name"].lower()
        
        if (equipe1_lower in home or equipe1_lower in away) and \
           (equipe2_lower in home or equipe2_lower in away):
            return match
    
    return None

def get_stats_match(fixture_id):
    """Récupère stats détaillées d'un match"""
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"id": fixture_id}
    
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        return r.json().get("response", [None])[0]
    except:
        return None

def get_h2h(team1_id, team2_id):
    """Récupère l'historique H2H"""
    url = "https://v3.football.api-sports.io/fixtures/headtohead"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"h2h": f"{team1_id}-{team2_id}", "last": 10}
    
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        return r.json().get("response", [])
    except:
        return []

def get_forme_equipe(team_id):
    """Récupère la forme récente d'une équipe"""
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    date_fin = heure_france().strftime('%Y-%m-%d')
    date_debut = (heure_france() - timedelta(days=60)).strftime('%Y-%m-%d')
    params = {
        "team": team_id,
        "from": date_debut,
        "to": date_fin,
        "last": 10
    }
    
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        return r.json().get("response", [])
    except:
        return []

def get_cotes_match(fixture_id):
    """Récupère les cotes du match"""
    url = "https://v3.football.api-sports.io/odds"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"fixture": fixture_id}
    
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        return r.json().get("response", [])
    except:
        return []

def compiler_donnees_match(match):
    """Compile toutes les données nécessaires pour l'analyse"""
    fixture_id = match["fixture"]["id"]
    home_id = match["teams"]["home"]["id"]
    away_id = match["teams"]["away"]["id"]
    
    print(f"Collecte des données pour {match['teams']['home']['name']} vs {match['teams']['away']['name']}...")
    
    stats_detaillees = get_stats_match(fixture_id)
    h2h = get_h2h(home_id, away_id)
    forme_home = get_forme_equipe(home_id)
    forme_away = get_forme_equipe(away_id)
    cotes = get_cotes_match(fixture_id)
    
    return {
        "match": match,
        "stats": stats_detaillees,
        "h2h": h2h,
        "forme_home": forme_home,
        "forme_away": forme_away,
        "cotes": cotes
    }

def analyser_avec_gemini(donnees, mode="rapide"):
    """Envoie les données à Gemini pour analyse"""
    
    match = donnees["match"]
    home = match["teams"]["home"]["name"]
    away = match["teams"]["away"]["name"]
    date_match = match["fixture"]["date"]
    ligue = match["league"]["name"]
    
    if mode == "rapide":
        prompt = f"""Tu es un expert en paris sportifs. Analyse ce match et donne UNIQUEMENT :

MATCH : {home} vs {away}
LIGUE : {ligue}
DATE : {date_match}

DONNÉES DISPONIBLES :
{str(donnees)}

RÉPONDS AU FORMAT SUIVANT (STRICTEMENT) :

📊 PARIS CONSEILLÉS
1. [Type de pari] — Cote [X.XX] — Confiance [X/10]
2. [Type de pari] — Cote [X.XX] — Confiance [X/10]

📋 ANALYSE RAPIDE (5-6 lignes maximum)
[Ton analyse concise]

⚠️ POINTS D'ATTENTION (2-3 points maximum)
- [Point 1]
- [Point 2]

Fais des recherches web pour vérifier :
- Compositions probables
- Blessures récentes
- Déclarations d'avant-match
- Mouvement des cotes

Sois CONCIS et DIRECT."""

    else:  # mode détaillé
        with open('/mnt/user-data/uploads/18_POINTS_ANALYSE_PARIS_SPORTIFS.txt', 'r', encoding='utf-8') as f:
            guide_18_points = f.read()
        
        prompt = f"""Tu es un expert en paris sportifs. Analyse ce match selon LES 18 POINTS du guide fourni.

MATCH : {home} vs {away}
LIGUE : {ligue}
DATE : {date_match}

GUIDE D'ANALYSE :
{guide_18_points}

DONNÉES COLLECTÉES :
{str(donnees)}

Fais des recherches web approfondies pour compléter ton analyse.

RÉPONDS DE MANIÈRE COMPLÈTE ET STRUCTURÉE selon les 18 points."""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Erreur Gemini : {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /start"""
    message = (
        "✅ Bot d'Analyse Paris Sportifs v17\n"
        "———————————————\n"
        "🎯 6 championnats couverts :\n"
        "• Ligue 1\n"
        "• Premier League\n"
        "• La Liga\n"
        "• Serie A\n"
        "• Bundesliga\n"
        "• Brasileirão\n"
        "———————————————\n"
        "💬 UTILISATION :\n\n"
        "Envoie juste : PSG Marseille\n"
        "→ Analyse rapide\n\n"
        "Envoie : PSG Marseille détails\n"
        "→ Analyse complète 18 points\n\n"
        "Envoie : matchs\n"
        "→ Liste des matchs à venir\n"
        "———————————————\n"
        "Prêt à analyser !"
    )
    await update.message.reply_text(message)

async def liste_matchs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste tous les matchs à venir"""
    await update.message.reply_text("🔍 Recherche des matchs à venir...")
    
    matchs = get_matchs_a_venir(jours=3)
    
    if not matchs:
        await update.message.reply_text("Aucun match trouvé dans les 3 prochains jours.")
        return
    
    # Grouper par date
    matchs_par_date = {}
    for match in matchs[:30]:  # Limite à 30 matchs
        date = match["fixture"]["date"][:10]
        if date not in matchs_par_date:
            matchs_par_date[date] = []
        matchs_par_date[date].append(match)
    
    message = "📅 MATCHS À VENIR\n\n"
    for date, matchs_jour in sorted(matchs_par_date.items())[:3]:
        message += f"📆 {date}\n"
        for m in matchs_jour[:10]:
            heure = m["fixture"]["date"][11:16]
            home = m["teams"]["home"]["name"]
            away = m["teams"]["away"]["name"]
            ligue = m["league"]["name"]
            message += f"{heure} • {home} vs {away} ({ligue})\n"
        message += "\n"
    
    await update.message.reply_text(message)

async def analyser_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyse un match demandé par l'utilisateur"""
    texte = update.message.text.strip()
    
    # Vérifier si c'est la commande "matchs"
    if texte.lower() == "matchs":
        await liste_matchs(update, context)
        return
    
    # Déterminer le mode
    mode = "rapide"
    if "détails" in texte.lower() or "details" in texte.lower():
        mode = "détaillé"
        texte = texte.replace("détails", "").replace("details", "").strip()
    
    # Parser les équipes
    parties = texte.split()
    if len(parties) < 2:
        await update.message.reply_text(
            "❌ Format incorrect\n\n"
            "Utilise : PSG Marseille\n"
            "Ou : PSG Marseille détails"
        )
        return
    
    equipe1 = parties[0]
    equipe2 = " ".join(parties[1:]) if len(parties) > 2 else parties[1]
    
    await update.message.reply_text(f"🔍 Recherche du match {equipe1} vs {equipe2}...")
    
    # Chercher le match
    match = chercher_match(equipe1, equipe2)
    
    if not match:
        await update.message.reply_text(
            f"❌ Match non trouvé\n\n"
            f"Aucun match trouvé entre '{equipe1}' et '{equipe2}' dans les 7 prochains jours.\n\n"
            f"Vérifie l'orthographe ou utilise /matchs pour voir les matchs disponibles."
        )
        return
    
    # Collecter les données
    await update.message.reply_text("📊 Collecte des statistiques...")
    donnees = compiler_donnees_match(match)
    
    # Analyser avec Gemini
    await update.message.reply_text(f"🤖 Analyse {'complète' if mode == 'détaillé' else 'rapide'} en cours...")
    analyse = analyser_avec_gemini(donnees, mode)
    
    # Envoyer le résultat
    home = match["teams"]["home"]["name"]
    away = match["teams"]["away"]["name"]
    date_match = match["fixture"]["date"]
    ligue = match["league"]["name"]
    
    header = f"🏆 {home} vs {away}\n📅 {date_match}\n🎯 {ligue}\n\n"
    
    await update.message.reply_text(header + analyse)

async def erreur_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère les erreurs"""
    print(f"Erreur : {context.error}")

def main():
    """Point d'entrée principal"""
    print("Bot Paris Sportifs v17 avec Gemini - Démarrage...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyser_match))
    application.add_error_handler(erreur_handler)
    
    print("Bot prêt et en écoute...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
