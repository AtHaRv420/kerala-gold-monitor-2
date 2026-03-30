# PRD: Kerala Gold WhatsApp Alert Bot

## 1. Context & Objectives
We are building a zero-maintenance, zero-hosting-cost WhatsApp bot for a user with minimal technical literacy. The sole purpose is to send a highly readable daily alert regarding 22K & 24K gold prices in Kerala, allowing the user to make timely purchasing decisions without manually checking rates.

## 2. Architecture & Constraints
* **Execution:** Must use a zero-cost Serverless environment with a Cron trigger (e.g., GitHub Actions or Vercel Cron). 
* **State Management:** DO NOT use local file storage (like `.json` files) as serverless environments are ephemeral. To calculate the 7-day rolling average/highs/lows, either fetch historical data directly via the Gold API, or use a free-tier serverless database (e.g., Upstash Redis or Supabase).
* **Pricing Logic:** DO NOT use a paid API or require any Gold API keys. You must use a robust HTML parser (e.g., BeautifulSoup for Python) to scrape the live retail prices directly from https://www.goodreturns.in/gold-rates/kerala.html. Extract the 1-gram and 8-gram (Pavan) rates for both 22K and 24K gold from the main daily data tables. Additionally, locate and parse the historical data table on that same page to extract the past 7 days of 22K and 24K prices to calculate the 7-day highs, lows, and yesterday's price. Ensure the scraping logic includes proper headers (User-Agent) to avoid being blocked.
* **Strict Prohibition:** Do not reference or attempt to recreate any legacy Python code previously used for this task. Write this from scratch based ONLY on this spec.

## 3. User Flow & Alert Logic
1.  **The Trigger:** A cron job runs daily at 10:00 AM IST and 6:00 PM IST. 
2.  **The Fetch:** The function calls the Gold API to get today's 22K 1-gram rate, 8-gram rate and retrieves the last 7 days of data for both 22K and 24K gold.
3.  **The Calculation:** The bot calculates the difference from yesterday, and identifies the 7-day High and 7-day Low.
4.  **The Delivery:** The bot formats the message and dispatches it via the WhatsApp API (Meta Business or Twilio).
5.  **Sandbox Session Management:** To handle the 72-hour Twilio Sandbox expiration, the bot must track the "Days Since Last Join." Every 3rd day, append a 'Maintenance' footer to the WhatsApp message.
    
    **Maintenance Footer:** "തുടർന്നും ഈ അപ്‌ഡേറ്റുകൾ ലഭിക്കാൻ താഴെയുള്ള ലിങ്കിൽ ക്ലിക്ക് ചെയ്ത് സെൻഡ് ചെയ്യുക: https://wa.me/14155238886?text=join%20bent-deal"
    
    **Logic:** 
    *   Take the current day of the year (1-365). If that number is divisible by 3, append the footer.
    *   **Contextual Hiding (Smart Evening Alerts):** During evening runs (12 PM+), use the Twilio API to check if the user has already sent an inbound message (replied) today. If they have, omit the footer for that specific user.

## 4. Message Structure (The Output)
The bot should send a localized, highly scannable message. Provide support for both English and a Malayalam translation.

**Template Structure:**
🥇 Kerala 22K Gold - Morning Update
💰 Today: ₹[Price]/gm
⚖️ 1 Pavan (8g): ₹[Price]
📊 Yesterday: ₹[Price]/gm
📈 Change: [+/- ₹Change]

🥇 Kerala 24K Gold - Morning Update
💰 Today: ₹[Price]/gm
⚖️ 1 Pavan (8g): ₹[Price]
📊 Yesterday: ₹[Price]/gm
📈 Change: [+/- ₹Change]

7-Day Range:
   High: ₹[High]
   Low: ₹[Low]

[Indicator Emoji & Text]
⏰ [Time] IST

**Indicator Logic:**
* If price drops by more than ₹100: "🔥 DIP ALERT! / വില കുറഞ്ഞു!"
* If price change is between -₹100 and +₹100: "✅ Stable / സ്ഥിരത"
* If price goes up by more than ₹100: "📈 Up / വില കൂടി"

## 5. Edge Cases to Handle
* **API Failure:** If the Gold API fails to respond or times out, do NOT send a WhatsApp message with `$0` or `null`. Fail silently or retry after 1 hour.
* **Weekend Stagnation:** If the API returns the exact same data for Saturday and Sunday, ensure the 7-day rolling average calculation does not break or divide by zero.

## 6. Observability & Admin Alerting
The system must never fail silently. It requires a two-tier routing system for messages based on the execution state:

* **Success State:** If the Gold API returns a valid 200 response and the payload is successfully parsed, send the formatted Gold Alert to the `USER_WHATSAPP` number.
* **Failure State (The Admin Alert):** If the Gold API times out, returns a 4xx/5xx error, or the JSON parsing fails, the script MUST catch the exception. 
    * It must instantly send an error message to the `ADMIN_WHATSAPP` number.
    * **Error Template:** "⚠️ GOLD BOT FAILED ⚠️\nEnvironment: GitHub Actions\nError: [Short Stack Trace or API Status Code]\nTime: [Timestamp]"
* **Cron Monitoring:** Include a lightweight ping to a free service like Healthchecks.io at the very end of the script. This ensures that if the serverless environment itself fails to spin up (e.g., GitHub Actions goes down), the Admin still receives an email alert that the Cron job missed its schedule.

## 7. Deployment & Environment
* **Platform:** The bot must be deployed as a Serverless Function (e.g., GitHub Actions Cron Job, Vercel Cron, or AWS Lambda).
* **Environment Variables:** All secrets (API Keys, WhatsApp Credentials) and configuration (Phone Numbers, Timezone) must be stored securely in Environment Variables. Do not hardcode credentials in the source code.

## 8. Code Quality & Documentation
* **Language:** Use Python 3.11+.
* **Dependencies:** Use a `requirements.txt` file and pin all dependencies to specific versions (e.g., `requests==2.31.0`).
* **Type Hinting:** Use Python type hints for all function arguments and return values, also make sure that the python script is run in a venv. 
* **Docstrings:** Include comprehensive docstrings for all functions explaining what they do, their parameters, and what they return.
* **README:** Include a detailed `README.md` with setup instructions, environment variable documentation, and deployment instructions.

## 9. Testing Requirements
* Implement a TEST_MODE environment variable. When set to true, the script must bypass the live Gold API and Twilio API, and instead use local mock data to validate the calculation logic and print the final WhatsApp message to the console.