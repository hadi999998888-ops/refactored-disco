import os, json, mimetypes, sqlite3, threading, time, hmac, hashlib, secrets, urllib.request, urllib.parse
import config
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'database.db'
MEDIA = ROOT / 'media'
PORT = int(os.getenv('PORT', '3000'))
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
ADMINS = {int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()}
MANAGER = os.getenv('MANAGER_USERNAME', 'GifterBroPro').strip().lstrip('@') or 'GifterBroPro'

SCHEMA = '''PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(telegram_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,created_at TEXT NOT NULL,language TEXT DEFAULT 'ru',display_name TEXT DEFAULT '',photo_path TEXT DEFAULT '',profile_done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,description TEXT NOT NULL,video_count INTEGER NOT NULL DEFAULT 0,price INTEGER NOT NULL,currency TEXT NOT NULL,cover_path TEXT DEFAULT '',active INTEGER DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_videos(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,file_path TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,file_name TEXT DEFAULT '',mime_type TEXT DEFAULT 'application/octet-stream');
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER NOT NULL REFERENCES products(id),user_id INTEGER NOT NULL,status TEXT NOT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER UNIQUE NOT NULL REFERENCES orders(id),user_id INTEGER NOT NULL,rating INTEGER NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admin_upload_tokens(token TEXT PRIMARY KEY,admin_id INTEGER NOT NULL,expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS download_tokens(token TEXT PRIMARY KEY,video_id INTEGER NOT NULL REFERENCES product_videos(id),user_id INTEGER NOT NULL,expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS web_login_tokens(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at INTEGER NOT NULL);
'''


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def now():
    return datetime.now(timezone.utc).isoformat()


def init():
    MEDIA.mkdir(exist_ok=True)
    with db() as c:
        c.executescript(SCHEMA)


def reply(h, code, data, headers=None):
    raw = json.dumps(data, ensure_ascii=False).encode()
    h.send_response(code)
    h.send_header('Content-Type', 'application/json; charset=utf-8')
    h.send_header('Content-Length', str(len(raw)))
    h.send_header('Access-Control-Allow-Origin', '*')
    h.send_header('Cache-Control', 'no-store')
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(raw)


def valid_admin_token(token):
    with db() as c:
        r = c.execute(
            'SELECT * FROM admin_upload_tokens WHERE token=?',
            (token,)
        ).fetchone()

    return r if r and r['expires_at'] > time.time() and r['admin_id'] in ADMINS else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('HTTP:', fmt % args)

    def read_json(self, limit=30000):
        n = int(self.headers.get('Content-Length', '0'))
        if n < 1 or n > limit:
            raise ValueError('bad length')
        return json.loads(self.rfile.read(n))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if path == '/':
            return self.file(ROOT / 'index.html', 'text/html; charset=utf-8')

        if path.lower() in ('/icon.jpg', '/icon.png'):
            return self.file(ROOT / 'Icon.png', 'image/png')

        if path == '/api/login':
            token = qs.get('token', [''])[0]
            with db() as c:
                row = c.execute(
                    'SELECT user_id,expires_at FROM web_login_tokens WHERE token=?',
                    (token,)
                ).fetchone() if token else None

                if row and row['expires_at'] >= time.time():
                    user = c.execute(
                        'SELECT telegram_id,profile_done FROM users WHERE telegram_id=?',
                        (row['user_id'],)
                    ).fetchone()
                    c.execute(
                        'DELETE FROM web_login_tokens WHERE token=?',
                        (token,)
                    )
                else:
                    user = None

            if not row or not user or not user['profile_done']:
                self.send_response(303)
                self.send_header('Location', '/')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return

            uid = int(user['telegram_id'])
            exp = int(time.time()) + 86400 * 7
            base = f'{uid}.{exp}'
            sig = hmac.new(
                BOT_TOKEN.encode(),
                base.encode(),
                hashlib.sha256
            ).hexdigest()

            self.send_response(303)
            self.send_header('Location', '/')
            self.send_header('Cache-Control', 'no-store')
            self.send_header(
                'Set-Cookie',
                f'lepot_session={base}.{sig}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800'
            )
            self.end_headers()
            return

        if path == '/api/config':
            return reply(
                self,
                200,
                {'bot_username': os.getenv('BOT_USERNAME', '').strip().lstrip('@')}
            )

        if path == '/api/me':
            u = self.current_user()
            return reply(self, 200, {'user': dict(u) if u else None})

        if path == '/api/products':
            with db() as c:
                ps = c.execute(
                    'SELECT * FROM products WHERE active=1 ORDER BY id DESC'
                ).fetchall()

            out = []

            for p in ps:
                with db() as c:
                    buyers = c.execute(
                        "SELECT u.telegram_id,u.display_name,u.photo_path,r.rating,r.text "
                        "FROM orders o JOIN users u ON u.telegram_id=o.user_id "
                        "LEFT JOIN reviews r ON r.order_id=o.id "
                        "WHERE o.product_id=? AND o.status='delivered'",
                        (p['id'],)
                    ).fetchall()

                out.append({
                    'id': p['id'],
                    'name': p['name'],
                    'description': p['description'],
                    'video_count': p['video_count'],
                    'price': p['price'],
                    'currency': p['currency'],
                    'cover': '/media/' + p['cover_path'],
                    'buyers': [dict(x) for x in buyers]
                })

            return reply(self, 200, {'products': out})

        if path == '/api/community':
            with db() as c:
                users = c.execute(
                    "SELECT display_name,photo_path FROM users WHERE profile_done=1 "
                    "ORDER BY created_at DESC LIMIT 100"
                ).fetchall()

            return reply(self, 200, {'users': [dict(x) for x in users]})

        if path == '/api/upload-info':
            t = valid_admin_token(qs.get('token', [''])[0])
            return reply(self, 200, {'ok': bool(t)})

        if path.startswith('/media/'):
            name = path[7:]
            if not name or Path(name).name != name:
                return self.send_error(404)
            return self.file(
                MEDIA / name,
                mimetypes.guess_type(name)[0] or 'application/octet-stream'
            )

        if path == '/download':
            tok = qs.get('token', [''])[0]
            with db() as c:
                r = c.execute(
                    'SELECT d.user_id,d.expires_at,v.file_path,v.file_name,v.mime_type '
                    'FROM download_tokens d '
                    'JOIN product_videos v ON v.id=d.video_id '
                    'WHERE d.token=?',
                    (tok,)
                ).fetchone()

            if not r or r['expires_at'] < time.time():
                return self.send_error(403)

            return self.file(
                MEDIA / r['file_path'],
                r['mime_type'] or 'application/octet-stream',
                download_name=r['file_name'] or r['file_path']
            )

        if path == '/admin-upload':
            return self.file(
                ROOT / 'upload.html',
                'text/html; charset=utf-8'
            )

        self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        # GitHub webhook endpoint
        if path == '/de4/api/webhooks/github':
            try:
                n = int(self.headers.get('Content-Length', '0'))

                if n < 1 or n > 30000:
                    return reply(
                        self,
                        400,
                        {'error': 'Invalid payload size'}
                    )

                payload = self.rfile.read(n)
                event = self.headers.get(
                    'X-GitHub-Event',
                    'unknown'
                )
                secret = os.getenv(
                    'GITHUB_WEBHOOK_SECRET',
                    ''
                ).encode()

                if secret:
                    signature = self.headers.get(
                        'X-Hub-Signature-256',
                        ''
                    )
                    digest = 'sha256=' + hmac.new(
                        secret,
                        payload,
                        hashlib.sha256
                    ).hexdigest()

                    if not hmac.compare_digest(signature, digest):
                        return reply(
                            self,
                            401,
                            {'error': 'Invalid signature'}
                        )

                print(f'GitHub webhook received: {event}')
                return reply(self, 200, {'ok': True})

            except Exception as e:
                print('GitHub webhook error:', e)
                return reply(
                    self,
                    400,
                    {'error': 'Invalid payload'}
                )

        if path == '/api/admin/create':
            t = valid_admin_token(qs.get('token', [''])[0])

            if not t:
                return reply(
                    self,
                    403,
                    {'error': 'Ссылка администратора истекла'}
                )

            try:
                d = self.read_json()
                name = str(d['name']).strip()[:100]
                desc = str(d.get('description', ''))[:1200]
                price = int(d.get('price', 0))
                cur = 'FREE' if price == 0 else str(d.get('currency', 'GIFTS')).upper()

                if cur not in ('FREE', 'GIFTS', 'XTR'):
                    cur = 'GIFTS'
            except Exception:
                return reply(
                    self,
                    400,
                    {'error': 'Проверьте поля товара'}
                )

            if not name or price < 0:
                return reply(
                    self,
                    400,
                    {'error': 'Некорректные данные товара'}
                )

            with db() as c:
                curid = c.execute(
                    'INSERT INTO products '
                    '(name,description,video_count,price,currency,created_at) '
                    'VALUES(?,?,?,?,?,?)',
                    (name, desc, 0, price, cur, now())
                ).lastrowid

            return reply(
                self,
                200,
                {'ok': True, 'product_id': curid}
            )

        if path.startswith('/api/admin/file/'):
            t = valid_admin_token(qs.get('token', [''])[0])

            if not t:
                return reply(
                    self,
                    403,
                    {'error': 'Ссылка администратора истекла'}
                )

            try:
                pid = int(path.rsplit('/', 1)[1])
                kind = qs.get('kind', ['video'])[0]
                filename = Path(
                    urllib.parse.unquote(
                        qs.get('name', ['material.bin'])[0]
                    )
                ).name[:180]
                n = int(self.headers.get('Content-Length', '0'))
            except Exception:
                return reply(
                    self,
                    400,
                    {'error': 'Некорректный запрос'}
                )

            if n < 1:
                return reply(
                    self,
                    400,
                    {'error': 'Файл пустой'}
                )

            with db() as c:
                p = c.execute(
                    'SELECT * FROM products WHERE id=? AND active=0',
                    (pid,)
                ).fetchone()

            if not p:
                return reply(
                    self,
                    404,
                    {'error': 'Черновик товара не найден'}
                )

            suffix = Path(filename).suffix[:12]
            safe = f'asset_{secrets.token_hex(12)}{suffix}'
            dest = MEDIA / safe
            remaining = n

            try:
                with dest.open('wb') as f:
                    while remaining:
                        chunk = self.rfile.read(
                            min(1024 * 1024, remaining)
                        )

                        if not chunk:
                            raise OSError('upload interrupted')

                        f.write(chunk)
                        remaining -= len(chunk)
            except Exception:
                dest.unlink(missing_ok=True)
                return reply(
                    self,
                    400,
                    {'error': 'Загрузка прервалась'}
                )

            mime = self.headers.get(
                'Content-Type',
                'application/octet-stream'
            ).split(';')[0]

            with db() as c:
                if kind == 'cover':
                    c.execute(
                        'UPDATE products SET cover_path=? WHERE id=?',
                        (safe, pid)
                    )
                else:
                    pos = c.execute(
                        'SELECT COUNT(*) n FROM product_videos WHERE product_id=?',
                        (pid,)
                    ).fetchone()['n']

                    c.execute(
                        'INSERT INTO product_videos '
                        '(product_id,file_path,position,file_name,mime_type) '
                        'VALUES(?,?,?,?,?)',
                        (pid, safe, pos, filename, mime)
                    )

            return reply(
                self,
                200,
                {'ok': True, 'name': filename, 'bytes': n}
            )

        if path == '/api/admin/finalize':
            t = valid_admin_token(qs.get('token', [''])[0])

            if not t:
                return reply(
                    self,
                    403,
                    {'error': 'Ссылка администратора истекла'}
                )

            try:
                pid = int(self.read_json()['product_id'])
            except Exception:
                return reply(
                    self,
                    400,
                    {'error': 'Нет ID товара'}
                )

            with db() as c:
                p = c.execute(
                    'SELECT * FROM products WHERE id=? AND active=0',
                    (pid,)
                ).fetchone()

                count = c.execute(
                    'SELECT COUNT(*) n FROM product_videos WHERE product_id=?',
                    (pid,)
                ).fetchone()['n']

                if not p or not p['cover_path'] or not count:
                    return reply(
                        self,
                        400,
                        {'error': 'Нужны обложка и хотя бы один файл'}
                    )

                c.execute(
                    'UPDATE products SET video_count=?,active=1 WHERE id=?',
                    (count, pid)
                )

            return reply(
                self,
                200,
                {'ok': True, 'count': count}
            )

        if path == '/api/auth':
            if not BOT_TOKEN:
                return reply(
                    self,
                    503,
                    {'error': 'BOT_TOKEN не настроен'}
                )

            try:
                d = self.read_json()
                auth_date = int(d['auth_date'])
                uid = int(d['id'])
                check = '\n'.join(
                    f'{k}={v}'
                    for k, v in sorted(d.items())
                    if k != 'hash'
                )
                secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
                expected = hmac.new(
                    secret,
                    check.encode(),
                    hashlib.sha256
                ).hexdigest()
            except Exception:
                return reply(
                    self,
                    401,
                    {'error': 'Ошибка входа Telegram'}
                )

            if abs(time.time() - auth_date) > 86400 or not hmac.compare_digest(
                expected,
                d.get('hash', '')
            ):
                return reply(
                    self,
                    401,
                    {'error': 'Не удалось проверить Telegram'}
                )

            with db() as c:
                c.execute(
                    'INSERT OR IGNORE INTO users '
                    '(telegram_id,username,first_name,created_at) '
                    'VALUES(?,?,?,?)',
                    (
                        uid,
                        d.get('username'),
                        d.get('first_name', ''),
                        now()
                    )
                )

                profile = c.execute(
                    'SELECT profile_done FROM users WHERE telegram_id=?',
                    (uid,)
                ).fetchone()

            if not profile or not profile['profile_done']:
                return reply(
                    self,
                    403,
                    {'error': 'Профиль ещё не завершён в боте'}
                )

            exp = int(time.time()) + 86400 * 7
            base = f'{uid}.{exp}'
            sig = hmac.new(
                BOT_TOKEN.encode(),
                base.encode(),
                hashlib.sha256
            ).hexdigest()

            cookie = (
                f'lepot_session={base}.{sig}; '
                'HttpOnly; SameSite=Lax; Path=/; '
                'Max-Age=604800'
            )

            return reply(
                self,
                200,
                {'ok': True},
                {'Set-Cookie': cookie}
            )

        if path == '/api/order':
            user = self.current_user()

            if not user:
                return reply(
                    self,
                    401,
                    {'error': 'Сначала откройте бота'}
                )

            try:
                pid = int(self.read_json()[
