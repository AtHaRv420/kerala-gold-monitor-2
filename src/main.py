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

def fetch_gold_data(test_mode=False):
    """Fetches and parses live and historical gold data from goodreturns."""
    if test_mode:
        print("Using local mock data for testing...")
        return {
            '22k': {
                'today_1g': 14430,
                'today_8g': 115440,
                'yday_1g': 14635,
                'change': -205,
                'high_7d': 14635,
                'low_7d': 14200
            },
            '24k': {
                'today_1g': 15742,
                'today_8g': 125936,
                'yday_1g': 15966,
                'change': -224,
                'high_7d': 15966,
                'low_7d': 15500
            }
        }
        
    url = "https://www.goodreturns.in/gold-rates/kerala.html"
    scraper = cloudscraper.create_scraper()
    response = scraper.get(url, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    
    tables = soup.find_all('table')
    if len(tables) < 4:
        raise ValueError("Could not locate the expected number of pricing tables on goodreturns.in.")
    
    t22k = None
    t24k = None
    for t in tables[:3]:
        text = t.get_text()
        if "22 Carat" in text or "22k" in text.lower():
            t22k = t
        if "24 Carat" in text or "24k" in text.lower():
            t24k = t
            
    if not t24k: t24k = tables[0]
    if not t22k: t22k = tables[1]
    
    # 1g rates
    today_22k_1g = clean_price(t22k.find_all('tr')[1].find_all('td')[1].text)
    today_24k_1g = clean_price(t24k.find_all('tr')[1].find_all('td')[1].text)
    
    yday_22k_1g = clean_price(t22k.find_all('tr')[1].find_all('td')[2].text)
    yday_24k_1g = clean_price(t24k.find_all('tr')[1].find_all('td')[2].text)
    
    # 8g rates (Pavan)
    today_22k_8g = clean_price(t22k.find_all('tr')[2].find_all('td')[1].text)
    today_24k_8g = clean_price(t24k.find_all('tr')[2].find_all('td')[1].text)

    # Historical Table (Table 3)
    history_table = tables[3]
    rows = history_table.find_all('tr')[1:] # Skip header
    
    hist_22k = []
    hist_24k = []
    
    for row in rows[:7]: # Get up to 7 days
        cols = row.find_all('td')
        if len(cols) >= 4:
            try:
                p24k = clean_price(cols[1].text)
                p22k = clean_price(cols[3].text)
                if p24k > 0: hist_24k.append(p24k)
                if p22k > 0: hist_22k.append(p22k)
            except Exception:
                continue
                
    if not hist_22k: hist_22k = [today_22k_1g]
    if not hist_24k: hist_24k = [today_24k_1g]

    return {
        '22k': {
            'today_1g': today_22k_1g,
            'today_8g': today_22k_8g,
            'yday_1g': yday_22k_1g,
            'change': today_22k_1g - yday_22k_1g,
            'high_7d': max(hist_22k),
            'low_7d': min(hist_22k)
        },
        '24k': {
            'today_1g': today_24k_1g,
            'today_8g': today_24k_8g,
            'yday_1g': yday_24k_1g,
            'change': today_24k_1g - yday_24k_1g,
            'high_7d': max(hist_24k),
            'low_7d': min(hist_24k)
        }
    }

def format_signed(num: int) -> str:
    """Formats a number with a plus or minus sign."""
    return f"+₹{num}" if num > 0 else f"-₹{abs(num)}" if num < 0 else "₹0"

def get_indicator(change: int) -> str:
    """Returns the visual indicator string based on price change."""
    if change <= -100:
        return "🔥 DIP ALERT! / വില കുറഞ്ഞു!"
    elif change >= 100:
        return "📈 Up / വില കൂടി"
    else:
        return "✅ Stable / സ്ഥിരത"

def generate_message(data: dict) -> str:
    """Generates the WhatsApp formatted message array."""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    time_str = now.strftime("%I:%M %p")
    
    # Determine Morning or Evening based on 24-hour hour (if hour < 12 it's morning)
    greeting = "Morning Update" if now.hour < 12 else "Evening Update"
    
    d22 = data['22k']
    d24 = data['24k']
    
    indicator = get_indicator(d22['change'])
    
    msg = f"🥇 Kerala 22K Gold - {greeting}\n"
    msg += f"💰 Today: ₹{d22['today_1g']}/gm\n"
    msg += f"⚖️ 1 Pavan (8g): ₹{d22['today_8g']}\n"
    msg += f"📊 Yesterday: ₹{d22['yday_1g']}/gm\n"
    msg += f"📈 Change: {format_signed(d22['change'])}\n\n"
    
    msg += f"🥇 Kerala 24K Gold - {greeting}\n"
    msg += f"💰 Today: ₹{d24['today_1g']}/gm\n"
    msg += f"⚖️ 1 Pavan (8g): ₹{d24['today_8g']}\n"
    msg += f"📊 Yesterday: ₹{d24['yday_1g']}/gm\n"
    msg += f"📈 Change: {format_signed(d24['change'])}\n\n"
    
    msg += "7-Day Range:\n"
    msg += f"   High: ₹{d22['high_7d']}\n"
    msg += f"   Low: ₹{d22['low_7d']}\n\n"
    
    msg += f"{indicator}\n"
    msg += f"⏰ {time_str} IST"
    
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
                    masked_number = target_num[:-4] + "****" if len(target_num) > 4 else "****"
                    print(f"Sending to {masked_number}...")
                    try:
                        send_whatsapp(message, target_num)
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
