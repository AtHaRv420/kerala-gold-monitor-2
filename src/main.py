import os
import sys
import datetime
import traceback
import cloudscraper
import pytz
import requests
from bs4 import BeautifulSoup
from twilio.rest import Client
from dotenv import load_dotenv

def clean_price(price_str: str) -> int:
    """Removes currency symbols and commas, and converts to integer."""
    cleaned = ''.join(c for c in price_str if c.isdigit())
    return int(cleaned) if cleaned else 0

def parse_price_and_delta(cell_text: str) -> tuple:
    """Parses cells like '₹14,673(-27)' or '₹ 1,07,600(200▼)' -> (price, delta).

    Delta may be missing; returns (price, 0) in that case. Bankbazaar uses ▼/▲ to
    indicate direction; goodreturns uses explicit -/+ signs inside the parens.
    """
    txt = cell_text.strip()
    if '(' in txt:
        price_part, delta_part = txt.split('(', 1)
        delta_part = delta_part.rstrip(')').strip()
    else:
        price_part, delta_part = txt, ''

    price = clean_price(price_part)

    if not delta_part:
        return price, 0

    is_down = '-' in delta_part or '▼' in delta_part or 'down' in delta_part.lower()
    delta_magnitude = clean_price(delta_part)
    delta = -delta_magnitude if is_down else delta_magnitude
    return price, delta

def _mock_data() -> dict:
    """Local mock data for TEST_MODE runs."""
    return {
        'source': 'mock',
        '22k': {
            'today_1g': 14430, 'today_8g': 115440, 'yday_1g': 14635,
            'change': -205, 'high_7d': 14635, 'low_7d': 14200,
        },
        '24k': {
            'today_1g': 15742, 'today_8g': 125936, 'yday_1g': 15966,
            'change': -224, 'high_7d': 15966, 'low_7d': 15500,
        },
    }

def fetch_from_goodreturns() -> dict:
    """Primary source. Kerala-specific rates from goodreturns.in.

    As of May 2026 the page ships two tables: [Gram|24K|22K|18K] with delta
    inline in the price cell, and a 10-day history [Date|24K|22K].
    """
    url = "https://www.goodreturns.in/gold-rates/kerala.html"
    scraper = cloudscraper.create_scraper()
    response = scraper.get(url, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')

    tables = soup.find_all('table')
    if len(tables) < 2:
        raise ValueError("goodreturns.in: expected at least 2 tables, found "
                         f"{len(tables)}.")

    price_table, history_table = tables[0], tables[1]
    price_rows = price_table.find_all('tr')
    if len(price_rows) < 3:
        raise ValueError("goodreturns.in: price table has too few rows.")

    # Row layout: [header, 1g, 8g, 10g, 100g]. Cols: [gram, 24K, 22K, 18K].
    row_1g = price_rows[1].find_all('td')
    row_8g = price_rows[2].find_all('td')
    today_24k_1g, delta_24k = parse_price_and_delta(row_1g[1].get_text())
    today_22k_1g, delta_22k = parse_price_and_delta(row_1g[2].get_text())
    today_24k_8g, _ = parse_price_and_delta(row_8g[1].get_text())
    today_22k_8g, _ = parse_price_and_delta(row_8g[2].get_text())

    yday_22k_1g = today_22k_1g - delta_22k
    yday_24k_1g = today_24k_1g - delta_24k

    hist_22k, hist_24k = [], []
    for row in history_table.find_all('tr')[1:8]:  # 7 days
        cols = row.find_all('td')
        if len(cols) >= 3:
            p24k, _ = parse_price_and_delta(cols[1].get_text())
            p22k, _ = parse_price_and_delta(cols[2].get_text())
            if p24k > 0: hist_24k.append(p24k)
            if p22k > 0: hist_22k.append(p22k)

    if not hist_22k: hist_22k = [today_22k_1g]
    if not hist_24k: hist_24k = [today_24k_1g]

    return {
        'source': 'goodreturns',
        '22k': {
            'today_1g': today_22k_1g, 'today_8g': today_22k_8g,
            'yday_1g': yday_22k_1g, 'change': delta_22k,
            'high_7d': max(hist_22k), 'low_7d': min(hist_22k),
        },
        '24k': {
            'today_1g': today_24k_1g, 'today_8g': today_24k_8g,
            'yday_1g': yday_24k_1g, 'change': delta_24k,
            'high_7d': max(hist_24k), 'low_7d': min(hist_24k),
        },
    }

def fetch_from_bankbazaar() -> dict:
    """Fallback source. Also Kerala-specific.

    Layout: two 4-column tables (one per purity, [Gram|Today|Yesterday|Change])
    followed by a 10-day history in 8g pricing. We identify the 22K vs 24K
    table by price (22K < 24K) rather than order to survive column reshuffles.
    """
    url = "https://www.bankbazaar.com/gold-rate-kerala.html"
    scraper = cloudscraper.create_scraper()
    response = scraper.get(url, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')

    tables = soup.find_all('table')
    if len(tables) < 3:
        raise ValueError("bankbazaar.com: expected at least 3 tables, found "
                         f"{len(tables)}.")

    def read_purity_table(table) -> dict:
        rows = table.find_all('tr')
        if len(rows) < 3:
            raise ValueError("bankbazaar.com: purity table has too few rows.")
        row_1g = rows[1].find_all('td')
        row_8g = rows[2].find_all('td')
        return {
            'today_1g': clean_price(row_1g[1].get_text()),
            'yday_1g': clean_price(row_1g[2].get_text()),
            'today_8g': clean_price(row_8g[1].get_text()),
        }

    a = read_purity_table(tables[0])
    b = read_purity_table(tables[1])
    # Lower price = 22K, higher = 24K. Resilient to table reordering.
    d22, d24 = (a, b) if a['today_1g'] < b['today_1g'] else (b, a)

    # History table is in 8g pricing; divide by 8 for 1g equivalent.
    # Cols: [Date, 22K(8g), 24K(8g)].
    hist_22k, hist_24k = [], []
    for row in tables[2].find_all('tr')[1:8]:
        cols = row.find_all('td')
        if len(cols) >= 3:
            p22k_8g, _ = parse_price_and_delta(cols[1].get_text())
            p24k_8g, _ = parse_price_and_delta(cols[2].get_text())
            if p22k_8g > 0: hist_22k.append(p22k_8g // 8)
            if p24k_8g > 0: hist_24k.append(p24k_8g // 8)

    if not hist_22k: hist_22k = [d22['today_1g']]
    if not hist_24k: hist_24k = [d24['today_1g']]

    return {
        'source': 'bankbazaar',
        '22k': {
            'today_1g': d22['today_1g'], 'today_8g': d22['today_8g'],
            'yday_1g': d22['yday_1g'],
            'change': d22['today_1g'] - d22['yday_1g'],
            'high_7d': max(hist_22k), 'low_7d': min(hist_22k),
        },
        '24k': {
            'today_1g': d24['today_1g'], 'today_8g': d24['today_8g'],
            'yday_1g': d24['yday_1g'],
            'change': d24['today_1g'] - d24['yday_1g'],
            'high_7d': max(hist_24k), 'low_7d': min(hist_24k),
        },
    }

def fetch_gold_data(test_mode=False) -> dict:
    """Strategy wrapper: goodreturns -> bankbazaar. Raises only if both fail."""
    if test_mode:
        print("Using local mock data for testing...")
        return _mock_data()

    sources = [
        ("goodreturns", fetch_from_goodreturns),
        ("bankbazaar", fetch_from_bankbazaar),
    ]
    errors = []
    for name, fn in sources:
        try:
            print(f"Trying source: {name}...")
            data = fn()
            print(f"Fetched from {name}.")
            return data
        except Exception as e:
            print(f"Source '{name}' failed: {e}")
            errors.append(f"{name}: {e}")
    raise RuntimeError("All gold data sources failed. " + " | ".join(errors))

def format_signed(num: int) -> str:
    """Formats a number with a plus or minus sign."""
    return f"+₹{num}" if num > 0 else f"-₹{abs(num)}" if num < 0 else "₹0"

def get_indicator(change: int) -> str:
    """Returns the visual indicator string based on price change."""
    if change <= -100:
        return "വില കുറഞ്ഞു (DIP)"
    elif change >= 100:
        return "വില കൂടി (UP)"
    else:
        return "സ്ഥിരത (STABLE)"

def generate_message(data: dict) -> str:
    """Generates the WhatsApp formatted message array."""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    time_str = now.strftime("%I:%M %p")
    
    # Determine Morning or Evening based on 24-hour hour (if hour < 12 it's morning)
    greeting = "രാവിലെത്തെ നിരക്ക്" if now.hour < 12 else "വൈകുന്നേരത്തെ നിരക്ക്"
    
    d22 = data['22k']
    d24 = data['24k']
    
    indicator = get_indicator(d22['change'])
    
    msg = f"കേരളം 22K സ്വർണ്ണം - {greeting}\n"
    msg += f"• ഇന്ന്: ₹{d22['today_1g']}/gm\n"
    msg += f"• 1 പവൻ (8g): ₹{d22['today_8g']}\n"
    msg += f"• ഇന്നലെ: ₹{d22['yday_1g']}/gm\n"
    msg += f"• മാറ്റം: {format_signed(d22['change'])}\n\n"
    
    msg += f"കേരളം 24K സ്വർണ്ണം - {greeting}\n"
    msg += f"• ഇന്ന്: ₹{d24['today_1g']}/gm\n"
    msg += f"• 1 പവൻ (8g): ₹{d24['today_8g']}\n"
    msg += f"• ഇന്നലെ: ₹{d24['yday_1g']}/gm\n"
    msg += f"• മാറ്റം: {format_signed(d24['change'])}\n\n"
    
    msg += "7 ദിവസത്തെ മാറ്റം:\n"
    msg += f"• 22K ഏറ്റവും കൂടിയ: ₹{d22['high_7d']}, ഏറ്റവും കുറഞ്ഞ: ₹{d22['low_7d']}\n"
    msg += f"• 24K ഏറ്റവും കൂടിയ: ₹{d24['high_7d']}, ഏറ്റവും കുറഞ്ഞ: ₹{d24['low_7d']}\n\n"
    
    msg += f"വിപണി നില: {indicator}\n"
    msg += f"സമയം: {time_str} IST"
    
    return msg

def send_whatsapp(body: str, to_number: str):
    """Sends a WhatsApp message via Twilio."""
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
    from_number = os.environ.get('TWILIO_FROM_NUMBER')
    
    if not (account_sid and auth_token and from_number):
        print("Missing Twilio credentials. Skipping notification.")
        return

    client = Client(account_sid, auth_token)
    message = client.messages.create(
        body=body,
        from_=from_number,
        to=to_number
    )
    print(f"Message sent! SID: {message.sid}")

def has_user_replied_today(target_num: str) -> bool:
    """Checks Twilio logs if the user has sent an inbound message today."""
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
    
    if not (account_sid and auth_token):
        return False
        
    client = Client(account_sid, auth_token)
    now_utc = datetime.datetime.now(pytz.utc).date()
    
    try:
        messages = client.messages.list(from_=target_num, date_sent=now_utc)
        return len(messages) > 0
    except Exception as e:
        print(f"Failed to fetch Twilio history: {e}")
        return False

def notify_admin_error(error_msg: str):
    """Sends an error alert to the administrator."""
    admin_number = os.environ.get('ADMIN_WHATSAPP')
    if not admin_number:
        return
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
    body = (
        "⚠️ GOLD BOT FAILED ⚠️\n"
        "Environment: GitHub Actions\n"
        f"Error: {error_msg}\n"
        f"Time: {timestamp}"
    )
    send_whatsapp(body, admin_number)

def main():
    load_dotenv()
    test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'
    
    try:
        print("Fetching gold data...")
        data = fetch_gold_data(test_mode=test_mode)
        
        print("Formatting message...")
        message = generate_message(data)
        
        if test_mode:
            print("--- TEST MODE ACTIVE ---")
            print(message)
            print("------------------------")
        else:
            # We fetch both Admin and User numbers to send the successful update to everyone
            user_whatsapp_env = os.environ.get('USER_WHATSAPP', '')
            admin_whatsapp_env = os.environ.get('ADMIN_WHATSAPP', '')
            
            # Combine all comma-separated numbers from both variables into a unique list
            all_raw_numbers = user_whatsapp_env.split(',') + admin_whatsapp_env.split(',')
            # Strip whitespace and remove empty strings
            valid_numbers = {num.strip() for num in all_raw_numbers if num.strip()}
            
            if valid_numbers:
                for target_num in valid_numbers:
                    # target_num looks like "whatsapp:+918208356504"
                    # We will mask everything after the "whatsapp:+" prefix
                    if target_num.startswith("whatsapp:+"):
                        masked_number = "whatsapp:+**********"
                    else:
                        masked_number = "**********"
                        
                    print(f"Sending to {masked_number}...")
                    
                    user_msg = message
                    # Twilio Sandbox Maintenance Logic
                    ist = pytz.timezone('Asia/Kolkata')
                    now_ist = datetime.datetime.now(ist)
                    day_of_year = now_ist.timetuple().tm_yday
                    
                    if day_of_year % 3 == 0:
                        is_evening = now_ist.hour >= 12
                        needs_footer = True
                        
                        if is_evening:
                            if has_user_replied_today(target_num):
                                needs_footer = False
                                
                        if needs_footer:
                            user_msg += "\n\nതുടർന്നും ഈ അപ്‌ഡേറ്റുകൾ ലഭിക്കാൻ താഴെയുള്ള ലിങ്കിൽ ക്ലിക്ക് ചെയ്ത് സെൻഡ് ചെയ്യുക: https://wa.me/14155238886?text=join%20bent-deal"

                    try:
                        send_whatsapp(user_msg, target_num)
                    except Exception as e:
                        print(f"Failed to send to {masked_number}: {e}")
            else:
                print("No valid WHATSAPP numbers found in .env. Cannot send update.")
                
            # Ping Healthchecks
            ping_url = os.environ.get('HEALTHCHECKS_PING_URL')
            if ping_url:
                try:
                    requests.get(ping_url, timeout=10)
                    print("Pinged Healthchecks.io successfully.")
                except Exception as e:
                    print(f"Failed to ping healthchecks: {e}")
                    
    except Exception as e:
        print("An error occurred!")
        trace = traceback.format_exc()
        print(trace)
        
        if not test_mode:
            short_error = str(e)[:100]
            notify_admin_error(short_error)
        sys.exit(1)

if __name__ == "__main__":
    main()
