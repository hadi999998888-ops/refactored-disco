LEPOT SHOP V2 — Bothost deployment

FILES
- server.py — HTTP site/API, Telegram login handoff, direct file upload and protected downloads.
- bot.py — Telegram bot, profile registration, orders, payment review, delivery and reviews.
- index.html — compact black/white storefront.
- upload.html — protected product upload panel.
- config.py — loads optional Token.env (Bothost environment variables take precedence).
- Icon.jpg — LEPOT SHOP logo used by the site.
- requirements.txt — Python dependencies.
- Token.env — local test configuration. Replace BOT_TOKEN before production; do not publish a real token.
- media/ — uploaded covers and materials; keep on persistent storage.

BOTHOST
Main file: server.py
Port: 3000
Python: 3.11+

Required environment variables:
BOT_TOKEN=your_bot_token
ADMIN_IDS=your_telegram_id
BOT_USERNAME=LepotShopBot
MANAGER_USERNAME=GifterBroPro
SITE_URL=https://lepotshop.bothost.tech
PORT=3000

If a variable exists in Bothost, it overrides the same key in Token.env.

TELEGRAM DOMAIN
In @BotFather use /setdomain, choose LEPOT SHOP BOT, and enter exactly:
lepotshop.bothost.tech
Do not include https:// or a trailing slash.

PROFILE / WEBSITE LOGIN
1. User completes the profile in the bot: language -> photo (can skip) -> display name.
2. After registration, the bot's "Open store" button contains a short-lived, one-time login token.
3. Opening the store through that button signs the user into the website automatically and loads the same profile from the shared SQLite database.
4. If the user already has an old bot menu, send /start again to receive a fresh store button.
5. If opening the website directly, use Telegram Login Widget as a fallback. The BotFather domain above is required for that fallback.

PRODUCTS
The admin sends /admin in the bot. The bot returns a protected upload URL valid for one hour. Add product name, description, price, cover and one or more material files. Files upload directly to Bothost in chunks; Telegram Bot API download limits do not apply to this upload route. Actual limits depend on Bothost storage, proxy/body-size settings and request timeouts.

ORDERS
- Paid order: site sends order details to the user in Telegram and shows the manager @GifterBroPro.
- Free order: bot provides a free delivery action.
- Paid order is delivered only after an admin confirms it in the bot.
- The user can then leave a review using /review ORDER_ID.
- Confirmed buyers and their reviews appear on the product card.

BOT COMMANDS
/start — profile and menu
/admin — admin upload panel (ADMIN_IDS only)
/setpayment TEXT — change payment instructions
/products — list products (admin only)
/hideproduct ID — hide a product
/showproduct ID — show a product
/review ORDER_ID — leave a review after delivery

IMPORTANT
- The test token in Token.env is retained as requested. Replace it with the real token only when ready. Never publish a real bot token in a public repository.
- Set Bothost environment variables to the same intended values if they are already configured there; those values take precedence over Token.env.
- Keep database.db and media/ on persistent storage when redeploying.
- No exact maximum upload size can be guaranteed from application code; Bothost's platform/proxy/storage quotas determine the real limit.
