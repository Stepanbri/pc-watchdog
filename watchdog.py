"""
Tento skript monitoruje webovou stránku s výsledky předmětu KIV/PC na ZČU
a odesílá Discord notifikace při změně hodnocení.
"""

import os
import json
import time
import requests
import logging
import platform
import sys
import re
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Automatická správa Chrome driverů
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType


# =============================================================================
# ANSI barvy pro výstup do konzole
# =============================================================================
class Colors:
    """ANSI escape kódy pro barevný výstup v terminálu."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


# =============================================================================
# Konfigurace logování
# =============================================================================
# Logy se zapisují do souboru i konzole (důležité pro Docker)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("monitor.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)


# =============================================================================
# Načtení konfigurace
# =============================================================================
load_dotenv()

try:
    with open('config.json', 'r', encoding='utf-8') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print(f"{Colors.FAIL}Soubor config.json nebyl nalezen!{Colors.ENDC}")
    exit(1)

# Cesty k datovým souborům
COOKIES_FILE = 'cookies.json'   # Uložené session cookies
HISTORY_FILE = 'history.json'   # Poslední známý stav hodnocení
USERS_FILE = 'users.json'       # Mapování student_id -> discord_user_id


def print_banner():
    """Vypíše úvodní banner aplikace."""
    banner = f"""{Colors.CYAN}{Colors.BOLD}
    WATCHDOG v3.5 (Docker Friendly) | {platform.system()}
    {Colors.ENDC}"""
    print(banner)


# =============================================================================
# Hlavní třída monitoru
# =============================================================================
class KIVMonitor:
    """
    Hlavní třída pro monitoring výsledků KIV/PC.
    
    Zodpovídá za:
    - HTTP session a správu cookies
    - Selenium login přes Shibboleth
    - Parsování HTML s výsledky
    - Detekci změn a odesílání notifikací
    """
    
    def __init__(self):
        """Inicializace session a načtení uložených dat."""
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': CONFIG.get('user_agent', 'Mozilla/5.0')})
        self.users_map = self.load_users()
        self.load_cookies()

    # -------------------------------------------------------------------------
    # Pomocné metody pro výstup a správu souborů
    # -------------------------------------------------------------------------
    
    def log_to_console(self, message, color=Colors.ENDC):
        """Vypisuje zprávy do konzole s časem a barvou."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"{Colors.BLUE}[{timestamp}]{Colors.ENDC} {color}{message}{Colors.ENDC}", flush=True)

    def load_users(self):
        """Načte mapování studentů na Discord ID ze souboru."""
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                self.log_to_console(f"Chyba pri nacitani users.json: {e}", Colors.FAIL)
        return {}

    def load_cookies(self):
        """Načte uložené cookies pro requests session."""
        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                    for cookie in cookies:
                        self.session.cookies.set(cookie['name'], cookie['value'])
            except Exception:
                pass

    def save_cookies(self, driver_cookies):
        """Uloží cookies ze Selenium driveru a aktualizuje requests session."""
        with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(driver_cookies, f, indent=4, ensure_ascii=False)
        self.session.cookies.clear()
        for cookie in driver_cookies:
            self.session.cookies.set(cookie['name'], cookie['value'])

    # -------------------------------------------------------------------------
    # Selenium - přihlášení přes Shibboleth SSO
    # -------------------------------------------------------------------------
    
    def get_driver(self, options):
        """Vytvoří Chrome WebDriver s automaticky staženým driverem."""
        system = platform.system()
        os.environ['WDM_LOG'] = '0'
        if system == "Linux":
            # V Linuxu (Docker) použijeme Chromium
            service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
        else:
            service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)

    def perform_login(self):
        """Provede přihlášení přes Shibboleth SSO pomocí Selenium."""
        self.log_to_console("Session vyprsela. Spoustim Selenium login...", Colors.WARNING)
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--log-level=3")

        driver = None
        try:
            driver = self.get_driver(chrome_options)
            driver.get(CONFIG['target_url'])
            
            # Detekce přihlašovací stránky
            if "Single Sign-On" in driver.title or "j_username" in driver.page_source:
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "j_username")))
                driver.find_element(By.NAME, "j_username").send_keys(os.getenv("ORION_USERNAME"))
                driver.find_element(By.NAME, "j_password").send_keys(os.getenv("ORION_PASSWORD"))
                driver.find_element(By.NAME, "_eventId_proceed").click()
                WebDriverWait(driver, 20).until(EC.url_contains("kiv.zcu.cz"))
                time.sleep(2)
                self.save_cookies(driver.get_cookies())
                self.log_to_console("Login uspesny.", Colors.GREEN)
            else:
                self.save_cookies(driver.get_cookies())
        except Exception as e:
            self.log_to_console(f"Chyba loginu: {e}", Colors.FAIL)
        finally:
            if driver: driver.quit()

    # -------------------------------------------------------------------------
    # HTTP - stahování a parsování stránek
    # -------------------------------------------------------------------------
    
    def get_page_content(self, url):
        """Stáhne obsah stránky, případně provede login při expiraci session."""
        try:
            response = self.session.get(url, allow_redirects=True, timeout=15)
            # Pokud jsme přesměrováni na login, provedeme přihlášení
            if "Single Sign-On" in response.text or ("<form" in response.text and "SAML" in response.text):
                self.perform_login()
                response = self.session.get(url, timeout=15) 
            response.encoding = response.apparent_encoding 
            return response.text
        except Exception as e:
            self.log_to_console(f"Chyba site (GET): {e}", Colors.FAIL)
            return None

    def get_stag_orion_login(self, student_id):
        """Získá Orion login studenta z STAG API podle osobního čísla."""
        try:
            url = f"https://stag-ws.zcu.cz/ws/services/rest2/orion/getOrionLoginByOsobniCislo?osCislo={student_id}"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                login = response.text.strip()
                return login if login else "NEZNÁMÉ"
            else:
                self.log_to_console(f"STAG API warning: Status {response.status_code}", Colors.WARNING)
                return "NEZNÁMÉ"
        except Exception as e:
            self.log_to_console(f"STAG API exception: {e}", Colors.FAIL)
            return "NEZNÁMÉ"

    def parse_results(self, html):
        """
        Parsuje HTML tabulku s výsledky.
        
        Returns:
            dict: Slovník {student_id: {tutor, sp_points, total_points, result}}
        """
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table', class_='timetable-tab')
        if not table: return {}

        results = {}
        for row in table.find_all('tr', id=True):
            cols = row.find_all('td')
            if len(cols) > 10:
                sp_points = cols[2].get_text(strip=True)
                if not sp_points: sp_points = "0"

                total_points = cols[9].get_text(strip=True)
                if not total_points: total_points = "0"
                
                res = cols[10].get_text(strip=True)
                if not res: res = "Nezadáno"

                results[row['id']] = {
                    "tutor": cols[1].get_text(strip=True),
                    "sp_points": sp_points,
                    "total_points": total_points,
                    "result": res
                }
        return results

    # -------------------------------------------------------------------------
    # Detail hodnocení studenta
    # -------------------------------------------------------------------------
    
    def get_assessment_detail(self, student_id):
        """Stáhne detailní stránku s hodnocením studenta."""
        url = f"https://www.kiv.zcu.cz/studies/predmety/pc/assess.php?SID={student_id}"
        html = self.get_page_content(url)
        
        if not html:
            return {"text": "Nepodarilo se nacist detail.", "date": "Neznamo", "pdf_url": ""}

        soup = BeautifulSoup(html, 'html.parser')
        
        # Textový komentář hodnocení
        text_area = soup.find('textarea')
        evaluation_text = text_area.get_text(strip=True) if text_area else "Zadny textovy komentar."

        # Datum odevzdání
        submission_date = "Neznamo"
        for b_tag in soup.find_all('b'):
            if "Datum odevzdání" in b_tag.get_text():
                next_input = b_tag.find_next('input')
                if next_input: submission_date = next_input.get('value', 'Neznamo')
                break
        
        # Odkaz na PDF dokumentaci
        pdf_link_tag = soup.find('a', href=re.compile(r'.*dokumentace.*\.pdf'))
        pdf_url = pdf_link_tag['href'] if pdf_link_tag else ""

        return {
            "text": evaluation_text,
            "date": submission_date,
            "pdf_url": pdf_url,
            "detail_url": url
        }

    # -------------------------------------------------------------------------
    # Discord notifikace
    # -------------------------------------------------------------------------
    
    def send_discord_notification(self, student_id, old_data, new_data, detail_data, is_test=False):
        """Odešle embed notifikaci na Discord webhook."""
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        
        if is_test:
            # Testovací zprávy jdou na testovací webhook
            webhook_url = os.getenv("DISCORD_TEST_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")
        
        if not webhook_url: return

        # Ping konkrétního uživatele
        discord_user_id = self.users_map.get(student_id)
        ping_content = ""
        if discord_user_id:
            ping_content = f"<@{discord_user_id}>"
        elif student_id == CONFIG.get('my_student_id'):
            fallback_ping = CONFIG.get('discord_user_id_to_ping')
            if fallback_ping: ping_content = f"<@{fallback_ping}>"

        orion_username = self.get_stag_orion_login(student_id)

        color = 16744448  # Oranžová (#ff8000)
        title_suffix = " (TEST)" if is_test else ""

        # Zkrácení dlouhého textu
        eval_text_codeblock = detail_data['text']
        if len(eval_text_codeblock) > 950:
            eval_text_codeblock = eval_text_codeblock[:950] + "... (zkráceno)"

        current_time = datetime.now().strftime('%d.%m.%Y %H:%M:%S')

        # Sestavení embed zprávy
        embed = {
            "author": {"name": "KIV/PC: VÝSLEDKY"},
            "title": f"ZMĚNA HODNOCENÍ{title_suffix}",
            "url": detail_data['detail_url'],
            "description": f"```\n{eval_text_codeblock}\n```",
            "color": color,
            "fields": [
                {"name": "O. ČÍSLO", "value": student_id, "inline": True},
                {"name": "ORION", "value": orion_username, "inline": True},
                {"name": "CVIČENÍ", "value": new_data.get('tutor', 'Neznamo'), "inline": True},
                {"name": "ČAS ODEVZDÁNÍ SP", "value": f"{detail_data['date']}\n-------------------->", "inline": False},
                {"name": "BODY SP", "value": f"{new_data['sp_points']}/70", "inline": True},
                {"name": "BODY CELKEM", "value": f"{new_data['total_points']}/100", "inline": True},
                {"name": "VÝSLEDEK", "value": new_data['result'], "inline": False},
                {"name": "ODKAZY", "value": f"[ODKAZ NA DOKUMENTACI]({detail_data['pdf_url']})\n[ODKAZ NA DETAIL OHODNOCENÍ]({detail_data['detail_url']})", "inline": False}
            ],
            "footer": {"text": f"Čas kontroly: {current_time}"}
        }

        payload = {
            "content": ping_content if (ping_content and (is_test or student_id == CONFIG.get('my_student_id') or discord_user_id)) else None,
            "embeds": [embed],
            "username": "KIV-PC Bot"
        }

        try:
            requests.post(webhook_url, json=payload)
            time.sleep(1)  # Rate limit ochrana
        except Exception as e:
            self.log_to_console(f"Chyba webhooku: {e}", Colors.FAIL)

    # -------------------------------------------------------------------------
    # Správa historie
    # -------------------------------------------------------------------------
    
    def load_history(self):
        """Načte poslední známý stav hodnocení."""
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: return {}
        return {}

    def save_history(self, data):
        """Uloží aktuální stav hodnocení."""
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    # -------------------------------------------------------------------------
    # Hlavní logika
    # -------------------------------------------------------------------------
    
    def run_startup_test(self):
        """Spustí testovací běh při startu aplikace."""
        my_id = CONFIG.get('my_student_id')
        self.log_to_console(f"Spoustim startovaci test pro ID: {my_id}...", Colors.CYAN)
        
        html = self.get_page_content(CONFIG['target_url'])
        if not html:
            self.log_to_console("Test selhal: nelze stahnout stranku.", Colors.FAIL)
            return

        data = self.parse_results(html)
        my_data = data.get(my_id)
        
        if my_data:
            self.log_to_console("Data studenta nalezena, stahuji detail a overuji STAG API...", Colors.CYAN)
            detail = self.get_assessment_detail(my_id)
            fake_old_data = {"result": "TEST_START"}
            self.send_discord_notification(my_id, fake_old_data, my_data, detail, is_test=True)
            self.log_to_console("Testovaci zprava odeslana.", Colors.GREEN)
        else:
            self.log_to_console(f"Tvoje ID {my_id} v tabulce nenalezeno.", Colors.WARNING)

    def check_for_changes(self):
        """Hlavní metoda: kontroluje změny v hodnocení a odesílá notifikace."""
        self.users_map = self.load_users()
        
        html = self.get_page_content(CONFIG['target_url'])
        if not html: return

        current_data = self.parse_results(html)
        if not current_data: return

        old_data = self.load_history()
        changes_detected = False
        my_id = CONFIG.get('my_student_id')

        for sid, s_data in current_data.items():
            old_s_data = old_data.get(sid)
            is_new = False
            is_update = False
            
            # Detekce nového záznamu
            if not old_s_data:
                if sid == my_id or sid in self.users_map:
                    is_new = True
                    old_s_data = {"result": "N/A", "sp_points": "?", "total_points": "?"}
            else:
                # Detekce změny v existujícím záznamu
                if (s_data['result'] != old_s_data['result'] or 
                    s_data['total_points'] != old_s_data['total_points']):
                    is_update = True

            if is_new or is_update:
                changes_detected = True

                # Výpis do konzole
                sys.stdout.write("\n")
                self.log_to_console(f"{'='*40}", Colors.WARNING)
                self.log_to_console(f"!!! ZMĚNA DETEKOVÁNA !!!", Colors.FAIL)
                self.log_to_console(f"{'='*40}", Colors.WARNING)
                self.log_to_console(f" Student:      {sid}", Colors.BOLD)
                self.log_to_console(f" Výsledek:     {old_s_data['result']} -> {s_data['result']}", Colors.CYAN)
                self.log_to_console(f" Body SP:      {s_data['sp_points']} / 70", Colors.BLUE)
                self.log_to_console(f" Body Celkem:  {s_data['total_points']} / 100", Colors.BLUE)
                self.log_to_console(f" Cvičící:      {s_data.get('tutor', 'Neznámo')}", Colors.ENDC)
                self.log_to_console(f"{'='*40}\n", Colors.WARNING)
                
                # Odeslání Discord notifikace
                detail = self.get_assessment_detail(sid)
                self.send_discord_notification(sid, old_s_data, s_data, detail)

        if changes_detected:
            self.save_history(current_data)
        else:
            self.log_to_console("Zadne zmeny.", Colors.GREEN)


# =============================================================================
# Vstupní bod aplikace
# =============================================================================
if __name__ == "__main__":
    print_banner()
    monitor = KIVMonitor()
    
    # 1. Startovací test - ověří funkčnost a odešle testovací zprávu
    monitor.run_startup_test()
    
    # 2. První ostrá kontrola
    monitor.check_for_changes()
    
    # 3. Hlavní smyčka - pravidelná kontrola
    interval = CONFIG.get('check_interval_seconds', 30)
    print(f"{Colors.CYAN}Interval kontroly nastaven na {interval} sekund.{Colors.ENDC}")
    
    try:
        while True:
            time.sleep(interval)
            monitor.check_for_changes()
    except KeyboardInterrupt:
        print(f"\n{Colors.FAIL}Ukoncuji Watchdog...{Colors.ENDC}")