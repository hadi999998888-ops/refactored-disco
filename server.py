import os, json, mimetypes, sqlite3, threading, time, hmac, hashlib, secrets, urllib.request, urllib.parse
import config
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DB = ROOT / "database.db"
MEDIA = ROOT / "media"
PORT = int(os.getenv("PORT", "3000"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
MANAGER = os.getenv("MANAGER_USERNAME", "GifterBroPro").strip().lstrip("@") or "GifterBroPro"

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(
 telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
 created_at TEXT NOT NULL, language TEXT DEFAULT 'ru',
 display_name TEXT DEFAULT '', photo_path TEXT DEFAULT '',
 profile_done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products(
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
 description TEXT NOT NULL, video_count INTEGER NOT NULL DEFAULT 0,
 price INTEGER NOT NULL, currency TEXT NOT NULL,
 cover_path TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_videos(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
 file_path TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
 file_name TEXT DEFAULT '', mime_type TEXT DEFAULT 'application/octet-stream'
);
CREATE TABLE IF NOT EXISTS orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 product_id INTEGER NOT NULL REFERENCES products(id),
 user_id INTEGER NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id INTEGER UNIQUE NOT NULL REFERENCES orders(id),
 user_id INTEGER NOT NULL, rating INTEGER NOT NULL,
 text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admin_upload_tokens(
 token TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS download_tokens(
 token TEXT PRIMARY KEY,
 video_id INTEGER NOT NULL REFERENCES product_videos(id),
 user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS web_login_tokens(
 token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
"""

def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def now():
    return datetime.now(timezone.utc).isoformat()

def init():
    MEDIA.mkdir(exist_ok=True)
    with db() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        for name, typ in [
            ("language", "TEXT DEFAULT 'ru'"),
            ("display_name", "TEXT DEFAULT ''"),
            ("photo_path", "TEXT DEFAULT ''"),
            ("profile_done", "INTEGER DEFAULT 0")
        ]:
            if name not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {name} {typ}")
        pcols = {r["name"] for r in c.execute("PRAGMA table_info(product_videos)")}
        for name, typ in [
            ("file_name", "TEXT DEFAULT ''"),
            ("mime_type", "TEXT DEFAULT 'application/octet-stream'")
        ]:
            if name not in pcols:
                c.execute(f"ALTER TABLE product_videos ADD COLUMN {name} {typ}")
        c.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            ("payment_details", f"Для оплаты напишите менеджеру @{MANAGER}. Он отправит актуальные реквизиты.")
        )

def reply(h, code, data, headers=None):
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(raw)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(raw)

def valid_admin_token(token):
    with db() as c:
        r = c.execute("SELECT * FROM admin_upload_tokens WHERE token=?", (token,)).fetchone()
    return r if r and r["expires_at"] > time.time() and r["admin_id"] in ADMINS else None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("HTTP:", fmt % args)

    def read_json(self, limit=30000):
        n = int(self.headers.get("Content-Length", "0"))
        if n < 1 or n > limit:
            raise ValueError("bad length")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            return self.file(ROOT / "index.html", "text/html; charset=utf-8")

        if path.lower() in ("/icon.jpg", "/icon.png"):
            for filename in ("Icon.jpg", "Icon.png"):
                icon = ROOT / filename
                if icon.exists():
                    return self.file(icon, mimetypes.guess_type(filename)[0] or "image/png")
            return self.send_error(404)

        if path == "/api/login":
            token = qs.get("token", [""])[0]
            with db() as c:
                row = c.execute(
                    "SELECT user_id,expires_at FROM web_login_tokens WHERE token=?", (token,)
                ).fetchone() if token else None
                if row and row["expires_at"] >= time.time():
                    user = c.execute(
                        "SELECT telegram_id,profile_done FROM users WHERE telegram_id=?",
                        (row["user_id"],)
                    ).fetchone()
                    c.execute("DELETE FROM web_login_tokens WHERE token=?", (token,))
                else:
                    user = None
            if not row or not user or not user["profile_done"]:
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            uid = int(user["telegram_id"])
            exp = int(time.time()) + 86400 * 7
            base = f"{uid}.{exp}"
            sig = hmac.new(BOT_TOKEN.encode(), base.encode(), hashlib.sha256).hexdigest()
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Set-Cookie",
                f"lepot_session={base}.{sig}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800"
            )
            self.end_headers()
            return

        if path == "/api/config":
            return reply(self, 200, {
                "bot_username": os.getenv("BOT_USERNAME", "").strip().lstrip("@")
            })

        if path == "/api/me":
            user = self.current_user()
            return reply(self, 200, {"user": dict(user) if user else None})

        if path == "/api/products":
            with db() as c:
                products = c.execute(
                    "SELECT * FROM products WHERE active=1 ORDER BY id DESC"
                ).fetchall()
            result = []
            for p in products:
                with db() as c:
                    buyers = c.execute(
                        "SELECT u.telegram_id,u.display_name,u.photo_path,r.rating,r.text "
                        "FROM orders o JOIN users u ON u.telegram_id=o.user_id "
                        "LEFT JOIN reviews r ON r.order_id=o.id "
                        "WHERE o.product_id=? AND o.status='delivered' ORDER BY o.id DESC",
                        (p["id"],)
                    ).fetchall()
                result.append({
                    "id": p["id"], "name": p["name"],
                    "description": p["description"], "video_count": p["video_count"],
                    "price": p["price"], "currency": p["currency"],
                    "cover": "/media/" + p["cover_path"],
                    "buyers": [dict(x) for x in buyers]
                })
            return reply(self, 200, {"products": result})

        if path == "/api/community":
            with db() as c:
                users = c.execute(
                    "SELECT display_name,photo_path FROM users WHERE profile_done=1 "
                    "ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            return reply(self, 200, {"users": [dict(x) for x in users]})

        if path == "/api/upload-info":
            return reply(self, 200, {"ok": bool(valid_admin_token(qs.get("token", [""])[0]))})

        if path.startswith("/media/"):
            name = path[7:]
            if not name or Path(name).name != name:
                return self.send_error(404)
            return self.file(MEDIA / name, mimetypes.guess_type(name)[0] or "application/octet-stream")

        if path == "/download":
            token = qs.get("token", [""])[0]
            with db() as c:
                row = c.execute(
                    "SELECT d.user_id,d.expires_at,v.file_path,v.file_name,v.mime_type "
                    "FROM download_tokens d JOIN product_videos v ON v.id=d.video_id "
                    "WHERE d.token=?", (token,)
                ).fetchone()
            if not row or row["expires_at"] < time.time():
                return self.send_error(403)
            return self.file(
                MEDIA / row["file_path"],
                row["mime_type"] or "application/octet-stream",
                download_name=row["file_name"] or row["file_path"]
            )

        if path == "/admin-upload":
            return self.file(ROOT / "upload.html", "text/html; charset=utf-8")

        return self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # GitHub webhook: accepts /api/webhooks/github and prefixed paths such as /de4/api/webhooks/github
        if path.rstrip("/").endswith("/api/webhooks/github"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 30000:
                    return reply(self, 400, {"error": "Invalid payload size"})
                payload = self.rfile.read(length)
                secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").encode()
                if secret:
                    signature = self.headers.get("X-Hub-Signature-256", "")
                    digest = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(signature, digest):
                        return reply(self, 401, {"error": "Invalid signature"})
                json.loads(payload.decode("utf-8"))
                print(
                    "GitHub webhook:",
                    self.headers.get("X-GitHub-Event", "unknown"),
                    self.headers.get("X-GitHub-Delivery", "")
                )
                return reply(self, 200, {"ok": True})
            except Exception as error:
                print("GitHub webhook error:", error)
                return reply(self, 400, {"error": "Invalid payload"})

        if path == "/api/admin/create":
            token = qs.get("token", [""])[0]
            if not valid_admin_token(token):
                return reply(self, 403, {"error": "Ссылка администратора истекла. Создайте новую через /admin."})
            try:
                data = self.read_json()
                name = str(data["name"]).strip()[:100]
                desc = str(data.get("description", ""))[:1200]
                price = int(data.get("price", 0))
                currency = "FREE" if price == 0 else str(data.get("currency", "GIFTS")).upper()
            except Exception:
                return reply(self, 400, {"error": "Проверьте поля товара"})
            if not name or price < 0 or currency not in ("FREE", "GIFTS", "XTR"):
                return reply(self, 400, {"error": "Некорректные данные товара"})
            with db() as c:
                product_id = c.execute(
                    "INSERT INTO products(name,description,video_count,price,currency,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (name, desc, 0, price, currency, now())
                ).lastrowid
            return reply(self, 200, {"ok": True, "product_id": product_id})

        if path.startswith("/api/admin/file/"):
            token = qs.get("token", [""])[0]
            if not valid_admin_token(token):
                return reply(self, 403, {"error": "Ссылка администратора истекла"})
            try:
                product_id = int(path.rsplit("/", 1)[1])
                kind = qs.get("kind", ["video"])[0]
                filename = Path(urllib.parse.unquote(qs.get("name", ["material.bin"])[0])).name[:180]
                length = int(self.headers.get("Content-Length", "0"))
            except Exception:
                return reply(self, 400, {"error": "Некорректный запрос"})
            if length < 1:
                return reply(self, 400, {"error": "Файл пустой"})
            with db() as c:
                product = c.execute(
                    "SELECT * FROM products WHERE id=? AND active=0", (product_id,)
                ).fetchone()
            if not product:
                return reply(self, 404, {"error": "Черновик товара не найден"})
            suffix = Path(filename).suffix[:12]
            safe_name = f"asset_{secrets.token_hex(12)}{suffix}"
            destination = MEDIA / safe_name
            remaining = length
            try:
                with destination.open("wb") as f:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError("upload interrupted")
                        f.write(chunk)
                        remaining -= len(chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                return reply(self, 400, {"error": "Загрузка прервалась. Повторите файл."})
            mime = self.headers.get("Content-Type", "application/octet-stream").split(";")[0]
            with db() as c:
                if kind == "cover":
                    c.execute("UPDATE products SET cover_path=? WHERE id=?", (safe_name, product_id))
                else:
                    position = c.execute(
                        "SELECT COUNT(*) n FROM product_videos WHERE product_id=?", (product_id,)
                    ).fetchone()["n"]
                    c.execute(
                        "INSERT INTO product_videos(product_id,file_path,position,file_name,mime_type) "
                        "VALUES(?,?,?,?,?)",
                        (product_id, safe_name, position, filename, mime)
                    )
            return reply(self, 200, {"ok": True, "name": filename, "bytes": length})

        if path == "/api/admin/finalize":
            token = qs.get("token", [""])[0]
            if not valid_admin_token(token):
                return reply(self, 403, {"error": "Ссылка администратора истекла"})
            try:
                product_id = int(self.read_json()["product_id"])
            except Exception:
                return reply(self, 400, {"error": "Нет ID товара"})
            with db() as c:
                product = c.execute(
                    "SELECT * FROM products WHERE id=? AND active=0", (product_id,)
                ).fetchone()
                count = c.execute(
                    "SELECT COUNT(*) n FROM product_videos WHERE product_id=?", (product_id,)
                ).fetchone()["n"]
                if not product or not product["cover_path"] or not count:
                    return reply(self, 400, {"error": "Нужны обложка и хотя бы один файл материала"})
                c.execute(
                    "UPDATE products SET video_count=?,active=1 WHERE id=?",
                    (count, product_id)
                )
                total = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
                names = [x["name"] for x in c.execute(
                    "SELECT name FROM products ORDER BY id DESC LIMIT 5"
                )]
            if total % 5 == 0:
                for admin_id in ADMINS:
                    try:
                        self.send_bot(admin_id, "🎉 Добавлено 5 товаров!\n" + "\n".join("• " + x for x in names))
                    except Exception as error:
                        print("Admin notification failed:", error)
            return reply(self, 200, {"ok": True, "count": count})

        if path == "/api/auth":
            if not BOT_TOKEN:
                return reply(self, 503, {"error": "BOT_TOKEN не настроен"})
            try:
                data = self.read_json()
                auth_date = int(data["auth_date"])
                uid = int(data["id"])
                check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if k != "hash")
                secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
                expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            except Exception:
                return reply(self, 401, {"error": "Ошибка входа Telegram"})
            if abs(time.time() - auth_date) > 86400 or not hmac.compare_digest(expected, data.get("hash", "")):
                return reply(self, 401, {"error": "Не удалось проверить Telegram"})
            with db() as c:
                c.execute(
                    "INSERT OR IGNORE INTO users(telegram_id,username,first_name,created_at) VALUES(?,?,?,?)",
                    (uid, data.get("username"), data.get("first_name", ""), now())
                )
                profile = c.execute(
                    "SELECT profile_done FROM users WHERE telegram_id=?", (uid,)
                ).fetchone()
            if not profile or not profile["profile_done"]:
                bot_username = os.getenv("BOT_USERNAME", "LepotShopBot").lstrip("@")
                return reply(self, 403, {
                    "error": f"Профиль ещё не завершён в боте. Откройте @{bot_username} и отправьте /start."
                })
            exp = int(time.time()) + 86400 * 7
            base = f"{uid}.{exp}"
            sig = hmac.new(BOT_TOKEN.encode(), base.encode(), hashlib.sha256).hexdigest()
            return reply(self, 200, {"ok": True}, {
                "Set-Cookie": f"lepot_session={base}.{sig}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800"
            })

        if path == "/api/order":
            user = self.current_user()
            if not user:
                return reply(self, 401, {"error": "Сначала откройте бота и завершите профиль"})
            try:
                product_id = int(self.read_json()["product_id"])
            except Exception:
                return reply(self, 400, {"error": "Товар не выбран"})
            with db() as c:
                product = c.execute(
                    "SELECT * FROM products WHERE id=? AND active=1", (product_id,)
                ).fetchone()
                if not product:
                    return reply(self, 404, {"error": "Товар недоступен"})
                order_id = c.execute(
                    "INSERT INTO orders(product_id,user_id,status,created_at,updated_at) "
                    "VALUES(?,?,'awaiting_payment',?,?)",
                    (product_id, user["telegram_id"], now(), now())
                ).lastrowid
                row = c.execute(
                    "SELECT value FROM settings WHERE key='payment_details'"
                ).fetchone()
                details = row["value"] if row else f"Для оплаты напишите @{MANAGER}"
            price_text = "БЕСПЛАТНО" if product["price"] == 0 else (
                str(product["price"]) + (" ⭐" if product["currency"] == "XTR" else " 🎁")
            )
            message = f"🧾 Заказ №{order_id}\n📦 {product['name']}\n💰 {price_text}\n\n"
            if product["price"] == 0:
                message += "Нажмите «Получить бесплатно» в боте."
                keyboard = {"inline_keyboard": [[
                    {"text": "🎁 Получить бесплатно", "callback_data": f"free:{order_id}"}
                ]]}
            else:
                message += f"Перейдите к менеджеру @{MANAGER} для оплаты.\n\n{details}"
                keyboard = {"inline_keyboard": [
                    [{"text": f"👤 Менеджер @{MANAGER}", "url": f"https://t.me/{MANAGER}"}],
                    [{"text": "✅ Я оплатил", "callback_data": f"paid:{order_id}"}]
                ]}
            try:
                self.send_bot(user["telegram_id"], message, keyboard)
            except Exception as error:
                print("Order Telegram notification failed:", error)
                return reply(self, 502, {"error": "Не удалось написать в бот. Сначала нажмите /start в LEPOT SHOP."})
            return reply(self, 200, {"ok": True, "message": "Заказ отправлен в Telegram"})

        return self.send_error(404)

    def current_user(self):
        token = ""
        for part in self.headers.get("Cookie", "").split(";"):
            if part.strip().startswith("lepot_session="):
                token = part.strip().split("=", 1)[1]
        try:
            uid, exp, signature = token.split(".")
            base = f"{uid}.{exp}"
            expected = hmac.new(BOT_TOKEN.encode(), base.encode(), hashlib.sha256).hexdigest()
            if int(exp) < time.time() or not hmac.compare_digest(signature, expected):
                return None
            with db() as c:
                return c.execute(
                    "SELECT * FROM users WHERE telegram_id=? AND profile_done=1", (int(uid),)
                ).fetchone()
        except Exception:
            return None

    def send_bot(self, chat_id, text, markup=None):
        if not BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN missing")
        data = {"chat_id": chat_id, "text": text}
        if markup:
            data["reply_markup"] = markup
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
            if not result.get("ok"):
                raise RuntimeError("Telegram API rejected message")

    def file(self, path, content_type, download_name=None):
        try:
            size = path.stat().st_size
            f = path.open("rb")
        except Exception:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("X-Content-Type-Options", "nosniff")
        if download_name:
            self.send_header(
                "Content-Disposition",
                "attachment; filename*=UTF-8''" + urllib.parse.quote(download_name)
            )
        self.end_headers()
        try:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except BrokenPipeError:
            pass
        finally:
            f.close()

def run():
    init()
    http_server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    print(f"HTTP server listening on 0.0.0.0:{PORT}")
    import bot
    bot.run_bot()

if __name__ == "__main__":
    run()
