import os
import json
import mimetypes
import sqlite3
import threading
import time
import hmac
import hashlib
import secrets
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import config

ROOT = Path(__file__).resolve().parent
DB = ROOT / "database.db"
MEDIA = ROOT / "media"
PORT = int(os.getenv("PORT", "3000"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMINS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
MANAGER = os.getenv("MANAGER_USERNAME", "GifterBroPro").strip().lstrip("@")

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users(
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL,
    language TEXT DEFAULT 'ru',
    display_name TEXT DEFAULT '',
    photo_path TEXT DEFAULT '',
    profile_done INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    video_count INTEGER NOT NULL DEFAULT 0,
    price INTEGER NOT NULL,
    currency TEXT NOT NULL,
    cover_path TEXT DEFAULT '',
    active INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_videos(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    file_name TEXT DEFAULT '',
    mime_type TEXT DEFAULT 'application/octet-stream'
);

CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER UNIQUE NOT NULL REFERENCES orders(id),
    user_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_upload_tokens(
    token TEXT PRIMARY KEY,
    admin_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS download_tokens(
    token TEXT PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES product_videos(id),
    user_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS web_login_tokens(
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""


def db():
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def now():
    return datetime.now(timezone.utc).isoformat()


def init():
    MEDIA.mkdir(exist_ok=True)

    with db() as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO settings VALUES('payment_details',?)",
            (f"Для оплаты напишите менеджеру @{MANAGER}.",),
        )


def reply(handler, code, data, headers=None):
    raw = json.dumps(data, ensure_ascii=False).encode()

    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Cache-Control", "no-store")

    for key, value in (headers or {}).items():
        handler.send_header(key, value)

    handler.end_headers()
    handler.wfile.write(raw)


def valid_admin_token(token):
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM admin_upload_tokens WHERE token=?",
            (token,),
        ).fetchone()

    return (
        row
        if row
        and row["expires_at"] > time.time()
        and row["admin_id"] in ADMINS
        else None
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("HTTP:", fmt % args)

    def read_json(self, limit=30000):
        length = int(self.headers.get("Content-Length", "0"))

        if length < 1 or length > limit:
            raise ValueError("bad length")

        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query
        )

        if path == "/":
            return self.file(
                ROOT / "index.html",
                "text/html; charset=utf-8",
            )

        if path.lower() in ("/icon.jpg", "/icon.png"):
            for filename in ("Icon.jpg", "Icon.png"):
                icon = ROOT / filename
                if icon.exists():
                    return self.file(
                        icon,
                        mimetypes.guess_type(filename)[0],
                    )
            return self.send_error(404)

        if path == "/api/login":
            token = query.get("token", [""])[0]

            with db() as connection:
                row = (
                    connection.execute(
                        "SELECT user_id,expires_at "
                        "FROM web_login_tokens WHERE token=?",
                        (token,),
                    ).fetchone()
                    if token
                    else None
                )

                if row and row["expires_at"] >= time.time():
                    user = connection.execute(
                        "SELECT telegram_id,profile_done "
                        "FROM users WHERE telegram_id=?",
                        (row["user_id"],),
                    ).fetchone()
                    connection.execute(
                        "DELETE FROM web_login_tokens WHERE token=?",
                        (token,),
                    )
                else:
                    user = None

            if not row or not user or not user["profile_done"]:
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            uid = int(user["telegram_id"])
            expires = int(time.time()) + 7 * 86400
            base = f"{uid}.{expires}"
            signature = hmac.new(
                BOT_TOKEN.encode(),
                base.encode(),
                hashlib.sha256,
            ).hexdigest()

            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Set-Cookie",
                f"lepot_session={base}.{signature}; "
                "HttpOnly; SameSite=Lax; Path=/; Max-Age=604800",
            )
            self.end_headers()
            return

        if path == "/api/config":
            return reply(
                self,
                200,
                {
                    "bot_username": os.getenv(
                        "BOT_USERNAME",
                        "",
                    ).strip().lstrip("@")
                },
            )

        if path == "/api/me":
            user = self.current_user()
            return reply(
                self,
                200,
                {"user": dict(user) if user else None},
            )

        if path == "/api/products":
            with db() as connection:
                products = connection.execute(
                    "SELECT * FROM products "
                    "WHERE active=1 ORDER BY id DESC"
                ).fetchall()

            result = []

            for product in products:
                with db() as connection:
                    buyers = connection.execute(
                        "SELECT u.telegram_id,u.display_name,u.photo_path,"
                        "r.rating,r.text "
                        "FROM orders o "
                        "JOIN users u ON u.telegram_id=o.user_id "
                        "LEFT JOIN reviews r ON r.order_id=o.id "
                        "WHERE o.product_id=? AND o.status='delivered'",
                        (product["id"],),
                    ).fetchall()

                result.append(
                    {
                        "id": product["id"],
                        "name": product["name"],
                        "description": product["description"],
                        "video_count": product["video_count"],
                        "price": product["price"],
                        "currency": product["currency"],
                        "cover": "/media/" + product["cover_path"],
                        "buyers": [dict(row) for row in buyers],
                    }
                )

            return reply(self, 200, {"products": result})

        if path == "/api/community":
            with db() as connection:
                users = connection.execute(
                    "SELECT display_name,photo_path "
                    "FROM users WHERE profile_done=1 "
                    "ORDER BY created_at DESC LIMIT 100"
                ).fetchall()

            return reply(
                self,
                200,
                {"users": [dict(user) for user in users]},
            )

        if path == "/api/upload-info":
            token = query.get("token", [""])[0]
            return reply(
                self,
                200,
                {"ok": bool(valid_admin_token(token))},
            )

        if path.startswith("/media/"):
            name = path[7:]

            if not name or Path(name).name != name:
                return self.send_error(404)

            media_file = MEDIA / name
            return self.file(
                media_file,
                mimetypes.guess_type(name)[0]
                or "application/octet-stream",
            )

        if path == "/download":
            token = query.get("token", [""])[0]

            with db() as connection:
                row = connection.execute(
                    "SELECT d.user_id,d.expires_at,"
                    "v.file_path,v.file_name,v.mime_type "
                    "FROM download_tokens d "
                    "JOIN product_videos v ON v.id=d.video_id "
                    "WHERE d.token=?",
                    (token,),
                ).fetchone()

            if not row or row["expires_at"] < time.time():
                return self.send_error(403)

            return self.file(
                MEDIA / row["file_path"],
                row["mime_type"] or "application/octet-stream",
                download_name=row["file_name"] or row["file_path"],
            )

        if path == "/admin-upload":
            return self.file(
                ROOT / "upload.html",
                "text/html; charset=utf-8",
            )

        self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query
        )

        # GitHub webhook
        if path == "/de4/api/webhooks/github":
            try:
                length = int(
                    self.headers.get("Content-Length", "0")
                )

                if length < 1 or length > 30000:
                    return reply(
                        self,
                        400,
                        {"error": "Invalid payload size"},
                    )

                payload = self.rfile.read(length)
                event = self.headers.get(
                    "X-GitHub-Event",
                    "unknown",
                )

                json.loads(payload.decode("utf-8"))

                print(f"GitHub webhook received: {event}")
                return reply(self, 200, {"ok": True})

            except Exception as error:
                print("GitHub webhook error:", error)
                return reply(
                    self,
                    400,
                    {"error": "Invalid payload"},
                )

        if path == "/api/admin/create":
            token = query.get("token", [""])[0]

            if not valid_admin_token(token):
                return reply(
                    self,
                    403,
                    {"error": "Ссылка администратора истекла"},
                )

            try:
                data = self.read_json()
                name = str(data["name"]).strip()[:100]
                description = str(
                    data.get("description", "")
                )[:1200]
                price = int(data.get("price", 0))
                currency = (
                    "FREE"
                    if price == 0
                    else str(data.get("currency", "GIFTS")).upper()
                )
            except Exception:
                return reply(
                    self,
                    400,
                    {"error": "Проверьте поля товара"},
                )

            if not name or price < 0 or currency not in (
                "FREE",
                "GIFTS",
                "XTR",
            ):
                return reply(
                    self,
                    400,
                    {"error": "Некорректные данные товара"},
                )

            with db() as connection:
                product_id = connection.execute(
                    "INSERT INTO products("
                    "name,description,video_count,"
                    "price,currency,created_at"
                    ") VALUES(?,?,?,?,?,?)",
                    (
                        name,
                        description,
                        0,
                        price,
                        currency,
                        now(),
                    ),
                ).lastrowid

            return reply(
                self,
                200,
                {"ok": True, "product_id": product_id},
            )

        if path.startswith("/api/admin/file/"):
            token = query.get("token", [""])[0]

            if not valid_admin_token(token):
                return reply(
                    self,
                    403,
                    {"error": "Ссылка администратора истекла"},
                )

            try:
                product_id = int(path.rsplit("/", 1)[1])
                kind = query.get("kind", ["video"])[0]
                filename = Path(
                    urllib.parse.unquote(
                        query.get("name", ["material.bin"])[0]
                    )
                ).name[:180]
                length = int(
                    self.headers.get("Content-Length", "0")
                )
            except Exception:
                return reply(
                    self,
                    400,
                    {"error": "Некорректный запрос"},
                )

            if length < 1:
                return reply(
                    self,
                    400,
                    {"error": "Файл пустой"},
                )

            with db() as connection:
                product = connection.execute(
                    "SELECT * FROM products "
                    "WHERE id=? AND active=0",
                    (product_id,),
                ).fetchone()

            if not product:
                return reply(
                    self,
                    404,
                    {"error": "Черновик товара не найден"},
                )

            suffix = Path(filename).suffix[:12]
            safe_name = (
                f"asset_{secrets.token_hex(12)}{suffix}"
            )
            destination = MEDIA / safe_name
            remaining = length

            try:
                with destination.open("wb") as file:
                    while remaining:
                        chunk = self.rfile.read(
                            min(1024 * 1024, remaining)
                        )
                        if not chunk:
                            raise OSError("upload interrupted")
                        file.write(chunk)
                        remaining -= len(chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                return reply(
                    self,
                    400,
                    {"error": "Загрузка прервалась"},
                )

            mime_type = self.headers.get(
                "Content-Type",
                "application/octet-stream",
            ).split(";")[0]

            with db() as connection:
                if kind == "cover":
                    connection.execute(
                        "UPDATE products SET cover_path=? WHERE id=?",
                        (safe_name, product_id),
                    )
                else:
                    position = connection.execute(
                        "SELECT COUNT(*) n "
                        "FROM product_videos "
                        "WHERE product_id=?",
                        (product_id,),
                    ).fetchone()["n"]

                    connection.execute(
                        "INSERT INTO product_videos("
                        "product_id,file_path,position,"
                        "file_name,mime_type"
                        ") VALUES(?,?,?,?,?)",
                        (
                            product_id,
                            safe_name,
                            position,
                            filename,
                            mime_type,
                        ),
                    )

            return reply(
                self,
                200,
                {
                    "ok": True,
                    "name": filename,
                    "bytes": length,
                },
            )

        if path == "/api/admin/finalize":
            token = query.get("token", [""])[0]

            if not valid_admin_token(token):
                return reply(
                    self,
                    403,
                    {"error": "Ссылка администратора истекла"},
                )

            try:
                product_id = int(
                    self.read_json()["product_id"]
                )
            except Exception:
                return reply(
                    self,
                    400,
                    {"error": "Нет ID товара"},
                )

            with db() as connection:
                product = connection.execute(
                    "SELECT * FROM products "
                    "WHERE id=? AND active=0",
                    (product_id,),
                ).fetchone()

                count = connection.execute(
                    "SELECT COUNT(*) n "
                    "FROM product_videos "
                    "WHERE product_id=?",
                    (product_id,),
                ).fetchone()["n"]

                if not product or not product["cover_path"] or not count:
                    return reply(
                        self,
                        400,
                        {
                            "error":
                            "Нужны обложка и хотя бы один файл"
                        },
                    )

                connection.execute(
                    "UPDATE products "
                    "SET video_count=?,active=1 "
                    "WHERE id=?",
                    (count, product_id),
                )

            return reply(
                self,
                200,
                {"ok": True, "count": count},
            )

        if path == "/api/auth":
            if not BOT_TOKEN:
                return reply(
                    self,
                    503,
              
