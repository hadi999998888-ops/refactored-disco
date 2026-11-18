import json, mimetypes, os, secrets, sqlite3, threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "database.db"
MEDIA_DIR = ROOT / "media"
PORT = int(os.getenv("PORT", "3000"))
MAX_REQUEST_BYTES = 16384
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL, video_count INTEGER NOT NULL, price INTEGER NOT NULL, currency TEXT NOT NULL, cover_path TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_videos(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, file_path TEXT NOT NULL, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id), user_id INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS purchase_tokens(token TEXT PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id), expires_at TEXT NOT NULL, redeemed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS delivery_history(order_id INTEGER PRIMARY KEY REFERENCES orders(id), delivered_at TEXT NOT NULL, video_count INTEGER NOT NULL);
"""
def connect_db():
    db=sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db
def init_db():
    MEDIA_DIR.mkdir(exist_ok=True)
    with connect_db() as db: db.executescript(SCHEMA)
def reply(h, code, data):
    raw=json.dumps(data,ensure_ascii=False).encode()
    h.send_response(code); h.send_header("Content-Type","application/json; charset=utf-8")
    h.send_header("Content-Length",str(len(raw))); h.send_header("Cache-Control","no-store"); h.end_headers(); h.wfile.write(raw)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt,*args): print("HTTP:",fmt%args)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/":
            return self.file(ROOT/"index.html","text/html; charset=utf-8")
        if path=="/icon.jpg":
            return self.file(ROOT/"Icon.jpg","image/jpeg")
        if path=="/api/products":
            with connect_db() as db:
                rows=db.execute("SELECT id,name,description,video_count,price,currency,cover_path FROM products WHERE active=1 ORDER BY id DESC").fetchall()
            return reply(self,200,{"products":[{"id":r["id"],"name":r["name"],"description":r["description"],"video_count":r["video_count"],"price":r["price"],"currency":r["currency"],"cover":"/media/"+r["cover_path"]} for r in rows]})
        if path.startswith("/media/"):
            name=path[len("/media/"):]
            if not name or Path(name).name!=name: return self.send_error(404)
            return self.file(MEDIA_DIR/name, mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_error(404)
    def do_POST(self):
        if urlparse(self.path).path!="/api/order-link": return self.send_error(404)
        try:
            n=int(self.headers.get("Content-Length","0"))
            if n<1 or n>MAX_REQUEST_BYTES: return reply(self,413,{"error":"Invalid request size"})
            if self.headers.get("Content-Type","").split(";")[0].strip()!="application/json": return reply(self,415,{"error":"JSON required"})
            payload=json.loads(self.rfile.read(n)); pid=int(payload["product_id"])
        except Exception: return reply(self,400,{"error":"Invalid request"})
        with connect_db() as db:
            row=db.execute("SELECT id FROM products WHERE id=? AND active=1",(pid,)).fetchone()
            if not row: return reply(self,404,{"error":"Product not found"})
            token=secrets.token_urlsafe(32)
            expiry=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat()
            db.execute("INSERT INTO purchase_tokens(token,product_id,expires_at) VALUES(?,?,?)",(token,pid,expiry))
        username=os.getenv("BOT_USERNAME","").strip().lstrip("@")
        if not username: return reply(self,503,{"error":"BOT_USERNAME is not configured"})
        return reply(self,200,{"url":f"https://t.me/{username}?start=buy_{token}"})
    def file(self,path,ctype):
        try: data=path.read_bytes()
        except OSError: return self.send_error(404)
        self.send_response(200); self.send_header("Content-Type",ctype); self.send_header("Content-Length",str(len(data))); self.send_header("X-Content-Type-Options","nosniff"); self.end_headers(); self.wfile.write(data)
def run():
    init_db()
    import bot
    threading.Thread(target=bot.run_bot,daemon=True,name="telegram-bot").start()
    srv=ThreadingHTTPServer(("0.0.0.0",PORT),Handler)
    print(f"LEPOT SHOP listening on {PORT}")
    srv.serve_forever()
if __name__=="__main__": run()
