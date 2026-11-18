import os, sqlite3, asyncio, secrets
from pathlib import Path
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

ROOT=Path(__file__).resolve().parent
DB=ROOT/"database.db"; MEDIA=ROOT/"media"
TOKEN=os.getenv("BOT_TOKEN","").strip()
ADMINS={int(x.strip()) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()}
BOT_USERNAME=os.getenv("BOT_USERNAME","").strip().lstrip("@")
MANAGER=os.getenv("MANAGER_USERNAME","").strip().lstrip("@")
SITE_URL=os.getenv("SITE_URL","").strip().rstrip("/")
STARS_ENABLED=os.getenv("STARS_ENABLED","false").lower()=="true"
def conn():
    c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def now(): return datetime.now(timezone.utc).isoformat()
def admin(uid): return uid in ADMINS
async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    with conn() as db: db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?)",(u.id,u.username,u.first_name,now()))
    arg=context.args[0] if context.args else ""
    if arg.startswith("buy_"):
        token=arg[4:]
        with conn() as db:
            row=db.execute("""SELECT t.product_id,p.name,p.description,p.price,p.currency,p.video_count
                FROM purchase_tokens t JOIN products p ON p.id=t.product_id
                WHERE t.token=? AND t.redeemed=0 AND t.expires_at>? AND p.active=1""",(token,now())).fetchone()
            if not row:
                await update.message.reply_text("Ссылка на покупку недействительна или устарела. Вернитесь на сайт и создайте заказ заново."); return
            db.execute("UPDATE purchase_tokens SET redeemed=1 WHERE token=?",(token,))
            cur=db.execute("INSERT INTO orders(product_id,user_id,status,created_at,updated_at) VALUES(?,?, 'awaiting_payment',?,?)",(row["product_id"],u.id,now(),now()))
            oid=cur.lastrowid
        kb=[[InlineKeyboardButton("💳 Перейти к менеджеру",url=f"https://t.me/{MANAGER}")] if MANAGER else [],
            [InlineKeyboardButton("✅ Я оплатил",callback_data=f"paid:{oid}")]]
        await update.message.reply_text(f"🛒 Заказ №{oid}\n\n📦 {row['name']}\n🎬 Видео: {row['video_count']}\n💰 Цена: {row['price']} {'⭐' if row['currency']=='XTR' else '🎁'}\n\nПерейдите к менеджеру для согласования оплаты, затем нажмите «Я оплатил».",reply_markup=InlineKeyboardMarkup([x for x in kb if x]))
        return
    kb=[[InlineKeyboardButton("📁 Перейти к покупкам",url=SITE_URL)] if SITE_URL else [],
        [InlineKeyboardButton("👤 Обращение к менеджеру",url=f"https://t.me/{MANAGER}")] if MANAGER else [],
        [InlineKeyboardButton("🤖 Статистика бота",callback_data="stats")]]
    await update.message.reply_text("👑 Добро пожаловать в наш магазин\n\n━━━━━━━━━━━━━━\n\nLEPOT SHOP🪐\n\nЧтобы приступить нажми на кнопку ниже",reply_markup=InlineKeyboardMarkup([x for x in kb if x]))
async def paid(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if q.data=="stats":
        with conn() as db:
            users=db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            products=db.execute("SELECT COUNT(*) n FROM products WHERE active=1").fetchone()["n"]
            orders=db.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
        await q.message.reply_text(f"🤖 Статистика LEPOT SHOP\nПользователей: {users}\nТоваров: {products}\nЗаказов: {orders}"); return
    oid=int(q.data.split(":")[1])
    with conn() as db:
        order=db.execute("SELECT o.*,p.name,p.price,p.currency FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=?",(oid,)).fetchone()
        if not order or order["user_id"]!=q.from_user.id or order["status"]!="awaiting_payment":
            await q.message.reply_text("Этот заказ уже отправлен на проверку или недоступен."); return
        db.execute("UPDATE orders SET status='payment_review',updated_at=? WHERE id=?",(now(),oid))
    for aid in ADMINS:
        try:
            await context.bot.send_message(aid,f"🔔 Проверка оплаты\nЗаказ №{oid}\nПокупатель: {q.from_user.full_name} (@{q.from_user.username or 'нет'})\nТовар: {order['name']}\nСумма: {order['price']} {order['currency']}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить",callback_data=f"approve:{oid}"),InlineKeyboardButton("❌ Отклонить",callback_data=f"reject:{oid}")]]))
        except Exception: pass
    await q.message.reply_text("Заявка отправлена менеджеру на проверку. После подтверждения бот выдаст видео.")
async def admin_actions(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if not admin(q.from_user.id): await q.answer("Нет доступа",show_alert=True); return
    action,oid_s=q.data.split(":"); oid=int(oid_s)
    with conn() as db:
        order=db.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        if not order or order["status"]!="payment_review": await q.message.reply_text("Заказ уже обработан."); return
        if action=="reject":
            db.execute("UPDATE orders SET status='rejected',updated_at=? WHERE id=?",(now(),oid))
            await context.bot.send_message(order["user_id"],f"Оплата по заказу №{oid} не подтверждена. Свяжитесь с менеджером.")
            await q.edit_message_text(f"Заказ №{oid}: отклонён."); return
        videos=db.execute("SELECT file_path FROM product_videos WHERE product_id=? ORDER BY position",(order["product_id"],)).fetchall()
        if not videos:
            await q.message.reply_text("У товара пока нет загруженных видео. Добавьте видео и повторите проверку."); return
        db.execute("UPDATE orders SET status='approved',updated_at=? WHERE id=?",(now(),oid))
        db.execute("INSERT OR IGNORE INTO delivery_history VALUES(?,?,?)",(oid,now(),len(videos)))
    sent=0
    for v in videos:
        path=MEDIA/v["file_path"]
        try:
            with path.open("rb") as f: await context.bot.send_video(order["user_id"],f)
            sent+=1
        except Exception: pass
    if sent==len(videos):
        with conn() as db: db.execute("UPDATE orders SET status='delivered',updated_at=? WHERE id=?",(now(),oid))
        await context.bot.send_message(order["user_id"],f"✅ Заказ №{oid} выполнен. Приятного просмотра!")
        await q.edit_message_text(f"Заказ №{oid}: оплата подтверждена, отправлено видео: {sent}.")
    else:
        await q.edit_message_text(f"Заказ №{oid}: подтверждён, но отправлено {sent} из {len(videos)} видео. Проверьте доставку вручную.")
async def admin_cmd(update,context):
    if not admin(update.effective_user.id): return
    await update.message.reply_text("Админ-команды:\n/addproduct — добавить товар\n/products — список товаров\n/stats — статистика")
async def addproduct(update,context):
    if not admin(update.effective_user.id): return
    context.user_data["new_product"]={}
    await update.message.reply_text("Отправьте название товара.")
    context.user_data["step"]="name"
async def products_cmd(update,context):
    if not admin(update.effective_user.id): return
    with conn() as db: rows=db.execute("SELECT id,name,price,currency,active FROM products ORDER BY id DESC").fetchall()
    await update.message.reply_text("\n".join([f"#{r['id']} {r['name']} — {r['price']} {r['currency']} ({'активен' if r['active'] else 'скрыт'})" for r in rows]) or "Товаров пока нет.")
async def text_steps(update,context):
    step=context.user_data.get("step")
    if not step or not admin(update.effective_user.id): return
    p=context.user_data["new_product"]
    msg=update.message
    if step=="name": p["name"]=msg.text.strip(); context.user_data["step"]="description"; await msg.reply_text("Описание товара:")
    elif step=="description": p["description"]=msg.text.strip(); context.user_data["step"]="count"; await msg.reply_text("Количество видео (число):")
    elif step=="count":
        try: p["count"]=int(msg.text); assert 1<=p["count"]<=100
        except: await msg.reply_text("Введите число от 1 до 100."); return
        context.user_data["step"]="price"; await msg.reply_text("Цена (целое число):")
    elif step=="price":
        try: p["price"]=int(msg.text); assert p["price"]>0
        except: await msg.reply_text("Введите положительное целое число."); return
        context.user_data["step"]="currency"; await msg.reply_text("Валюта: XTR (Stars) или GIFTS (подарки).")
    elif step=="currency":
        cur=msg.text.strip().upper()
        if cur not in ("XTR","GIFTS"): await msg.reply_text("Введите XTR или GIFTS."); return
        p["currency"]=cur; context.user_data["step"]="cover"; await msg.reply_text("Отправьте фотографию-обложку.")
async def photo_step(update,context):
    if context.user_data.get("step")!="cover" or not admin(update.effective_user.id): return
    p=context.user_data["new_product"]; f=await update.message.photo[-1].get_file()
    name=f"cover_{secrets.token_hex(8)}.jpg"; await f.download_to_drive(MEDIA/name); p["cover"]=name
    context.user_data["step"]="videos"; context.user_data["video_files"]=[]
    await update.message.reply_text(f"Обложка сохранена. Теперь отправьте {p['count']} видео по одному. После каждого видео бот сообщит прогресс.")
async def video_step(update,context):
    if context.user_data.get("step")!="videos" or not admin(update.effective_user.id): return
    p=context.user_data["new_product"]; f=await update.message.video.get_file()
    name=f"video_{secrets.token_hex(8)}.mp4"; await f.download_to_drive(MEDIA/name)
    arr=context.user_data["video_files"]; arr.append(name)
    if len(arr)<p["count"]:
        await update.message.reply_text(f"Получено {len(arr)}/{p['count']}. Отправьте следующее видео."); return
    with conn() as db:
        cur=db.execute("INSERT INTO products(name,description,video_count,price,currency,cover_path,created_at) VALUES(?,?,?,?,?,?,?)",(p["name"],p["description"],p["count"],p["price"],p["currency"],p["cover"],now()))
        pid=cur.lastrowid
        for i,path in enumerate(arr): db.execute("INSERT INTO product_videos(product_id,file_path,position) VALUES(?,?,?)",(pid,path,i))
    context.user_data.clear()
    await update.message.reply_text(f"✅ Товар «{p['name']}» добавлен на сайт. ID: {pid}")
async def stats_cmd(update,context):
    if not admin(update.effective_user.id): return
    with conn() as db:
        u=db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        p=db.execute("SELECT COUNT(*) n FROM products WHERE active=1").fetchone()["n"]
        o=db.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
    await update.message.reply_text(f"Пользователей: {u}\nАктивных товаров: {p}\nЗаказов: {o}")
def run_bot():
    if not TOKEN: raise RuntimeError("BOT_TOKEN is not set in Bothost environment")
    MEDIA.mkdir(exist_ok=True)
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("admin",admin_cmd))
    app.add_handler(CommandHandler("addproduct",addproduct))
    app.add_handler(CommandHandler("products",products_cmd))
    app.add_handler(CommandHandler("stats",stats_cmd))
    app.add_handler(CallbackQueryHandler(paid,pattern=r"^(paid:\d+|stats)$"))
    app.add_handler(CallbackQueryHandler(admin_actions,pattern=r"^(approve|reject):\d+$"))
    app.add_handler(MessageHandler(filters.PHOTO,photo_step))
    app.add_handler(MessageHandler(filters.VIDEO,video_step))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_steps))
    app.run_polling(drop_pending_updates=True, stop_signals=None)
