import requests
import time
import logging
import threading
from bs4 import BeautifulSoup
import os
from datetime import datetime
import trafilatura
from flask import Flask, jsonify

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'),
              logging.StreamHandler()])
logger = logging.getLogger(__name__)

# Création de l'application Flask pour l'endpoint /health
app = Flask(__name__)

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

def start_flask_app():
    # Démarrage du serveur Flask en mode production simple
    # On écoute sur toutes les interfaces sur le port 8080 (compatible Replit)
    app.run(host='0.0.0.0', port=8080)

class AppointmentBot:

    def __init__(self):
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')

        self.urls = [
            os.getenv('URL_1', 'https://www.rdv-prefecture.interieur.gouv.fr/rdvpref/reservation/demarche/2381/'),
            os.getenv('URL_2', 'https://www.rdv-prefecture.interieur.gouv.fr/rdvpref/reservation/demarche/3260/')
        ]

        self.check_interval = int(os.getenv('CHECK_INTERVAL', '60'))

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1'
        })

        self.previous_states = {}

        logger.info("Bot initialisé avec succès")
        logger.info(f"URLs surveillées: {self.urls}")
        logger.info(f"Intervalle de vérification: {self.check_interval} secondes")

    def send_telegram_message(self, message):
        if not self.telegram_token or not self.chat_id:
            logger.warning("Token Telegram ou Chat ID manquant")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = self.session.post(url, data=data, timeout=10)
            response.raise_for_status()
            logger.info(f"Message Telegram envoyé: {message[:50]}...")
            return True
        except Exception as e:
            logger.error(f"Erreur envoi Telegram: {e}")
            return False

    def extract_appointments_info(self, url):
        try:
            for attempt in range(3):
                try:
                    if attempt > 0:
                        time.sleep(2 + attempt * 2)
                    response = self.session.get(url, timeout=20, allow_redirects=True)
                    if response.status_code == 403:
                        logger.warning(f"Accès refusé (403) pour {url}, tentative {attempt + 1}/3")
                        if attempt < 2:
                            continue
                    response.raise_for_status()
                    if 'cloudflare' in response.text.lower() and ('blocked' in response.text.lower() or 'challenge' in response.text.lower()):
                        logger.warning(f"Bloqué par Cloudflare sur {url}")
                        if attempt < 2:
                            continue
                    break
                except requests.exceptions.RequestException as e:
                    if attempt == 2:
                        raise e
                    logger.warning(f"Erreur tentative {attempt + 1} pour {url}: {e}")

            soup = BeautifulSoup(response.content, 'html.parser')

            downloaded = trafilatura.fetch_url(url)
            clean_text = trafilatura.extract(downloaded) if downloaded else ""

            appointment_keywords = [
                'disponible', 'available', 'créneau', 'slot', 'appointment',
                'rendez-vous', 'booking', 'réserver', 'book', 'libre',
                'choisir', 'sélectionner', 'horaire', 'date', 'heure',
                'planning', 'agenda', 'calendrier', 'prendre rendez-vous'
            ]
            no_slots_indicators = [
                'aucun créneau', 'pas de créneau', 'indisponible',
                'complet', 'plus de place', 'aucune disponibilité',
                'pas de rendez-vous', 'service indisponible',
                'temporarily unavailable', 'maintenance'
            ]

            appointment_elements = []

            common_selectors = [
                '.appointment', '.slot', '.available', '.booking',
                '.calendar-day', '.time-slot', '.date-picker',
                '[data-available="true"]', '[data-status="available"]',
                '.rdv-slot', '.rdv-available', '.horaire-dispo',
                'button[data-date]', 'a[data-date]', '.btn-rdv',
                '.planning-slot', '.agenda-item', '.reservation-btn'
            ]

            for selector in common_selectors:
                elements = soup.select(selector)
                appointment_elements.extend(elements)

            available_slots = []
            has_no_slots = False

            if clean_text:
                text_lower = clean_text.lower()
                for indicator in no_slots_indicators:
                    if indicator in text_lower:
                        has_no_slots = True
                        break
                for keyword in appointment_keywords:
                    if keyword in text_lower:
                        lines = clean_text.split('\n')
                        for line in lines:
                            if keyword in line.lower() and len(line.strip()) > 5:
                                available_slots.append(line.strip())

            for element in appointment_elements:
                text = element.get_text(strip=True)
                if text and 2 < len(text) < 200:
                    available_slots.append(text)

            booking_elements = soup.find_all(['button', 'a', 'div'],
                class_=lambda x: x and any(keyword in str(x).lower() for keyword in
                ['rdv', 'reservation', 'booking', 'créneau', 'horaire', 'disponible']))

            for element in booking_elements:
                text = element.get_text(strip=True)
                if text and 2 < len(text) < 100:
                    available_slots.append(text)

            date_elements = soup.find_all(text=lambda text: text and any(
                date_word in text.lower() for date_word in [
                    'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
                    'juillet', 'août', 'septembre', 'octobre', 'novembre',
                    'décembre', 'jan', 'feb', 'mar', 'apr', 'may', 'jun',
                    'jul', 'aug', 'sep', 'oct', 'nov', 'dec', '2024', '2025',
                    '/', 'h', ':'
                ]))

            for date_element in date_elements:
                if date_element.strip():
                    available_slots.append(date_element.strip())

            return {
                'url': url,
                'slots': list(set(available_slots))[:10],
                'total_found': len(set(available_slots)),
                'page_title': soup.title.string if soup.title else 'Page sans titre',
                'timestamp': datetime.now().isoformat()
            }

        except requests.exceptions.Timeout:
            logger.error(f"Timeout lors de l'accès à {url}")
            return {'url': url, 'error': 'Timeout'}
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur réseau pour {url}: {e}")
            if '403' in str(e) or 'Forbidden' in str(e):
                return {'url': url, 'error': 'Site protégé par des mesures anti-bot', 'status': 'blocked'}
            return {'url': url, 'error': f'Erreur réseau: {e}'}
        except Exception as e:
            logger.error(f"Erreur analyse {url}: {e}")
            return {'url': url, 'error': f'Erreur analyse: {e}'}

    def check_appointments(self):
        logger.info("Début de la vérification des créneaux")
        for url in self.urls:
            try:
                logger.info(f"Vérification de {url}")
                appointment_info = self.extract_appointments_info(url)
                if 'error' in appointment_info:
                    logger.error(f"Erreur pour {url}: {appointment_info['error']}")
                    continue

                url_hash = hash(url)
                current_slots = set(appointment_info['slots'])
                previous_slots = self.previous_states.get(url_hash, set())
                new_slots = current_slots - previous_slots

                if new_slots:
                    message = f"🚨 <b>Nouveaux créneaux détectés!</b>\n\n"
                    message += f"📍 <b>Site:</b> {appointment_info['page_title']}\n"
                    message += f"🔗 <b>URL:</b> {url}\n\n"
                    message += f"📅 <b>Créneaux disponibles:</b>\n"
                    for slot in list(new_slots)[:5]:
                        message += f"• {slot}\n"
                    if len(new_slots) > 5:
                        message += f"... et {len(new_slots) - 5} autres créneaux\n"
                    message += f"\n⏰ <b>Détecté le:</b> {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}"
                    if self.send_telegram_message(message):
                        logger.info(f"Alerte envoyée pour {len(new_slots)} nouveaux créneaux sur {url}")

                self.previous_states[url_hash] = current_slots
                logger.info(f"Analyse terminée pour {url}: {len(current_slots)} créneaux trouvés")

            except Exception as e:
                logger.error(f"Erreur lors de la vérification de {url}: {e}")

        logger.info("Fin de la vérification des créneaux")

    def run_monitoring(self):
        logger.info("Démarrage de la surveillance continue")
        start_message = (f"🤖 <b>Bot de surveillance démarré!</b>\n\n"
                         f"📊 <b>URLs surveillées:</b> {len(self.urls)}\n"
                         f"⏱️ <b>Intervalle:</b> {self.check_interval} secondes\n"
                         f"🕐 <b>Démarré le:</b> {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}")
        self.send_telegram_message(start_message)

        while True:
            try:
                self.check_appointments()
                logger.info(f"Prochaine vérification dans {self.check_interval} secondes")
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                logger.info("Arrêt demandé par l'utilisateur")
                break
            except Exception as e:
                logger.error(f"Erreur dans la boucle principale: {e}")
                time.sleep(30)

def main():
    logger.info("Démarrage du bot de surveillance des rendez-vous")

    required_vars = ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Variables d'environnement manquantes: {missing_vars}")
        logger.info("Veuillez configurer les variables d'environnement suivantes:")
        logger.info("- TELEGRAM_BOT_TOKEN: Token de votre bot Telegram")
        logger.info("- TELEGRAM_CHAT_ID: ID du chat Telegram")
        logger.info("- URL_1: Première URL à surveiller (optionnel)")
        logger.info("- URL_2: Deuxième URL à surveiller (optionnel)")
        logger.info("- CHECK_INTERVAL: Intervalle en secondes (défaut: 60)")
        return

    bot = AppointmentBot()

    # Lancement du serveur Flask dans un thread daemon
    flask_thread = threading.Thread(target=start_flask_app, daemon=True)
    flask_thread.start()

    # Lancement de la surveillance dans un thread daemon
    monitoring_thread = threading.Thread(target=bot.run_monitoring, daemon=True)
    monitoring_thread.start()

    logger.info("Bot de surveillance et serveur web lancés en arrière-plan")

    # Maintien du programme principal actif
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Arrêt du programme demandé")

if __name__ == "__main__":
    main()
