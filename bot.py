import os, sqlite3, secrets, time, urllib.parse
import config
from pathlib import Path
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
ROOT=Path(__file__).resolve().parent; DB=ROOT/'database.db'; MEDIA=ROOT/'media'
TOKEN=os.getenv('BOT_TOKEN','').strip(); ADMINS={int(x.strip()) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip().isdigit()}; SITE=os.getenv('SITE_URL','').strip().rstrip('/'); MANAGER=os.getenv('MANAGER_USERNAME','GifterBroPro').strip().lstrip('@') or 'GifterBroPro'
def conn():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def now(): return datetime.now(timezone.utc).isoformat()
def isadmin(uid): return uid in ADMINS
def main_kb(uid):
 # Одноразовая ссылка связывает уже созданный профиль Telegram с сайтом без повторной регистрации.
 if SITE:
  web_token=secrets.token_urlsafe(32)
  with conn() as c: c.execute('INSERT INTO web_login_tokens(token,user_id,expires_at) VALUES(?,?,?)',(web_token,uid,int(time.time())+600))
  shop_url=SITE+'/api/login?token='+urllib.parse.quote(web_token)
  shop_row=[InlineKeyboardButton('🛍 Открыть магазин',url=shop_url)]
 else: shop_row=[InlineKeyboardButton('🛍 Магазин',callback_data='shop')]
 rows=[shop_row,[InlineKeyboardButton('👤 Мой профиль',callback_data='profile')]]
 if isadmin(uid): rows.append([InlineKeyboardButton('👑 Админка',callback_data='admin')])
 return InlineKeyboardMarkup(rows)
async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
 u=update.effective_user
 with conn() as c:
  c.execute('INSERT OR IGNORE INTO users(telegram_id,username,first_name,created_at) VALUES(?,?,?,?)',(u.id,u.username,u.first_name,now())); r=c.execute('SELECT profile_done FROM users WHERE telegram_id=?',(u.id,)).fetchone()
 if not r['profile_done']:
  context.user_data['profile_step']='language'
  await update.message.reply_text('Добро пожаловать в LEPOT SHOP!\n\nСоздадим профиль. Выберите язык:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🇷🇺 Русский',callback_data='lang:ru'),InlineKeyboardButton('🇬🇧 English',callback_data='lang:en')]])); return
 await update.message.reply_text('🪐 LEPOT SHOP\nПрофиль готов. Выберите действие:',reply_markup=main_kb(u.id))
async def admin_link(update,context):
 uid=update.effective_user.id
 if not isadmin(uid): return
 token=secrets.token_urlsafe(32); exp=int(time.time())+3600
 with conn() as c: c.execute('INSERT INTO admin_upload_tokens(token,admin_id,expires_at) VALUES(?,?,?)',(token,uid,exp))
 if SITE: await update.message.reply_text('👑 Админка LEPOT SHOP\n\nСоздание и управление товарами — в защищённой панели. Ссылка действует 1 час:\n'+SITE+'/admin-upload?token='+token)
 else: await update.message.reply_text('Добавьте SITE_URL в переменные Bothost.')
async def callback(update,context):
 q=update.callback_query; await q.answer(); uid=q.from_user.id; d=q.data
 if d.startswith('lang:'):
  lang=d.split(':')[1]
  with conn() as c: c.execute('UPDATE users SET language=? WHERE telegram_id=?',(lang,uid))
  context.user_data['profile_step']='photo'; await q.message.reply_text('Отправьте фото профиля или нажмите «Пропустить».',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Пропустить',callback_data='skipphoto')]])); return
 if d=='skipphoto': context.user_data['profile_step']='name'; await q.message.reply_text('Введите имя для профиля:'); return
 if d=='admin': await admin_link_from_callback(q); return
 if d=='profile':
  with conn() as c: u=c.execute('SELECT * FROM users WHERE telegram_id=?',(uid,)).fetchone()
  await q.message.reply_text(f"👤 Профиль\nИмя: {u['display_name'] or u['first_name']}\nЯзык: {u['language']}\nID: {uid}",reply_markup=main_kb(uid)); return
 if d=='shop':
  if SITE: await q.message.reply_text('Магазин:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Открыть LEPOT SHOP',url=SITE)]]))
  return
 if d.startswith('paid:'):
  oid=int(d.split(':')[1])
  with conn() as c:
   o=c.execute('SELECT o.*,p.name,p.price,p.currency FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=? AND o.user_id=?',(oid,uid)).fetchone()
   if not o or o['status']!='awaiting_payment': await q.message.reply_text('Заказ уже отправлен на проверку или недоступен.'); return
   c.execute("UPDATE orders SET status='payment_review',updated_at=? WHERE id=?",(now(),oid))
   user=c.execute('SELECT display_name FROM users WHERE telegram_id=?',(uid,)).fetchone()
  await notify_admins(context,f"🔔 Проверка оплаты\nЗаказ №{oid}\nПокупатель: {user['display_name']} (@{q.from_user.username or 'нет'})\nТовар: {o['name']}\nСумма: {o['price']} {o['currency']}",oid); await q.message.reply_text('Заявка отправлена на проверку.'); return
 if d.startswith('approve:') or d.startswith('reject:'):
  if not isadmin(uid): return
  action,oid_s=d.split(':'); oid=int(oid_s)
  with conn() as c:
   o=c.execute('SELECT o.*,p.name,p.price FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=?',(oid,)).fetchone()
   if not o or o['status']!='payment_review': await q.message.reply_text('Заказ уже обработан.'); return
   if action=='reject':
    c.execute("UPDATE orders SET status='rejected',updated_at=? WHERE id=?",(now(),oid)); await context.bot.send_message(o['user_id'],f'Оплата заказа №{oid} не подтверждена. Напишите менеджеру @{MANAGER}.'); await q.edit_message_text(f'Заказ №{oid}: отклонён.'); return
   files=c.execute('SELECT id,file_path,file_name,mime_type FROM product_videos WHERE product_id=? ORDER BY position',(o['product_id'],)).fetchall()
   c.execute("UPDATE orders SET status='delivered',updated_at=? WHERE id=?",(now(),oid))
  links=[]
  for f in files:
   tok=secrets.token_urlsafe(24)
   with conn() as c: c.execute('INSERT INTO download_tokens(token,video_id,user_id,expires_at) VALUES(?,?,?,?)',(tok,f['id'],o['user_id'],int(time.time())+7*86400))
   links.append(f"📎 {f['file_name'] or f['file_path']}: {SITE}/download?token={tok}")
  await context.bot.send_message(o['user_id'],f"✅ Заказ №{oid} подтверждён! Материалы доступны по ссылкам:\n\n"+'\n'.join(links)+'\n\nПосле просмотра оставьте отзыв: /review '+str(oid))
  await q.edit_message_text(f'Заказ №{oid}: подтверждён, выдано ссылок: {len(links)}.'); return
 if d.startswith('free:'):
  oid=int(d.split(':')[1])
  with conn() as c:
   o=c.execute("SELECT o.*,p.name,p.price FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=? AND o.user_id=?",(oid,uid)).fetchone()
   if not o or o['price']!=0 or o['status']!='awaiting_payment': await q.message.reply_text('Бесплатный заказ недоступен или уже получен.'); return
   fs=c.execute('SELECT id,file_path,file_name FROM product_videos WHERE product_id=? ORDER BY position',(o['product_id'],)).fetchall(); c.execute("UPDATE orders SET status='delivered',updated_at=? WHERE id=?",(now(),oid))
  links=[]
  for f in fs:
   tok=secrets.token_urlsafe(24)
   with conn() as c: c.execute('INSERT INTO download_tokens(token,video_id,user_id,expires_at) VALUES(?,?,?,?)',(tok,f['id'],uid,int(time.time())+7*86400))
   links.append(f"📎 {f['file_name'] or f['file_path']}: {SITE}/download?token={tok}")
  await q.message.reply_text('🎁 Бесплатный товар!\n\n'+'\n'.join(links)+'\n\nОставьте отзыв командой /review '+str(oid)); return
async def admin_link_from_callback(q):
 uid=q.from_user.id
 if not isadmin(uid): return
 token=secrets.token_urlsafe(32); exp=int(time.time())+3600
 with conn() as c: c.execute('INSERT INTO admin_upload_tokens(token,admin_id,expires_at) VALUES(?,?,?)',(token,uid,exp))
 await q.message.reply_text('👑 Защищённая панель создания товаров (1 час):\n'+SITE+'/admin-upload?token='+token)
async def notify_admins(context,text,oid):
 for aid in ADMINS:
  try: await context.bot.send_message(aid,text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Подтвердить',callback_data=f'approve:{oid}'),InlineKeyboardButton('❌ Отклонить',callback_data=f'reject:{oid}')]]))
  except Exception as e: print('admin notify:',e)
async def photo(update,context):
 uid=update.effective_user.id; step=context.user_data.get('profile_step')
 if step!='photo': return
 f=await update.message.photo[-1].get_file(); name=f'avatar_{uid}_{secrets.token_hex(5)}.jpg'; await f.download_to_drive(MEDIA/name)
 with conn() as c: c.execute('UPDATE users SET photo_path=? WHERE telegram_id=?',(name,uid))
 context.user_data['profile_step']='name'; await update.message.reply_text('Фото сохранено. Теперь введите имя для профиля:')
async def text(update,context):
 uid=update.effective_user.id; step=context.user_data.get('profile_step'); msg=update.message.text.strip()
 if step=='name':
  if not msg or len(msg)>40: await update.message.reply_text('Имя должно быть от 1 до 40 символов.'); return
  with conn() as c: c.execute('UPDATE users SET display_name=?,profile_done=1 WHERE telegram_id=?',(msg,uid))
  context.user_data.pop('profile_step',None); await update.message.reply_text('✅ Профиль создан!',reply_markup=main_kb(uid)); return
 if msg.startswith('/review'):
  parts=msg.split(maxsplit=1)
  if len(parts)<2 or not parts[1].isdigit(): await update.message.reply_text('Формат: /review НОМЕР_ЗАКАЗА'); return
  context.user_data['review_order']=int(parts[1]); context.user_data['review_step']=True; await update.message.reply_text('Напишите отзыв (до 500 символов):'); return
 if context.user_data.get('review_step'):
  oid=context.user_data.pop('review_order',None); context.user_data.pop('review_step',None)
  with conn() as c:
   o=c.execute("SELECT * FROM orders WHERE id=? AND user_id=? AND status='delivered'",(oid,uid)).fetchone()
   if not o: await update.message.reply_text('Заказ не найден или ещё не выдан.'); return
   c.execute('INSERT OR REPLACE INTO reviews(order_id,user_id,rating,text,created_at) VALUES(?,?,?,?,?)',(oid,uid,5,msg[:500],now()))
  await update.message.reply_text('Спасибо! Отзыв появится на странице товара.'); return
async def products_cmd(update,context):
 if not isadmin(update.effective_user.id): return
 with conn() as c: rows=c.execute('SELECT id,name,active,price,currency FROM products ORDER BY id DESC LIMIT 50').fetchall()
 if not rows: await update.message.reply_text('Товаров пока нет.'); return
 await update.message.reply_text('Товары LEPOT SHOP:\n'+'\n'.join(f"#{r['id']} · {r['name']} · {'активен' if r['active'] else 'скрыт'} · {r['price']} {r['currency']}" for r in rows)+'\n\nСкрыть: /hideproduct ID\nВернуть: /showproduct ID')
async def product_visibility(update,context):
 if not isadmin(update.effective_user.id): return
 if not context.args or not context.args[0].isdigit(): await update.message.reply_text('Укажите ID товара.'); return
 pid=int(context.args[0]); active=1 if update.message.text.startswith('/showproduct') else 0
 with conn() as c: cur=c.execute('UPDATE products SET active=? WHERE id=?',(active,pid))
 await update.message.reply_text('Готово.' if cur.rowcount else 'Товар не найден.')
async def review_cmd(update,context):
 if not context.args or not context.args[0].isdigit():
  await update.message.reply_text('Формат: /review НОМЕР_ЗАКАЗА'); return
 context.user_data['review_order']=int(context.args[0]); context.user_data['review_step']=True
 await update.message.reply_text('Напишите отзыв (до 500 символов):')
async def setpayment(update,context):
 if not isadmin(update.effective_user.id): return
 val=' '.join(context.args).strip()
 if not val: await update.message.reply_text('Использование: /setpayment текст реквизитов или инструкции'); return
 with conn() as c: c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('payment_details',?)",(val[:3000],))
 await update.message.reply_text('✅ Инструкция по оплате обновлена.')
def run_bot():
 if not TOKEN: raise RuntimeError('BOT_TOKEN is not set in Bothost environment')
 MEDIA.mkdir(exist_ok=True); app=Application.builder().token(TOKEN).build()
 app.add_handler(CommandHandler('start',start)); app.add_handler(CommandHandler('admin',admin_link)); app.add_handler(CommandHandler('setpayment',setpayment)); app.add_handler(CommandHandler('review',review_cmd)); app.add_handler(CommandHandler('products',products_cmd)); app.add_handler(CommandHandler('hideproduct',product_visibility)); app.add_handler(CommandHandler('showproduct',product_visibility)); app.add_handler(CallbackQueryHandler(callback)); app.add_handler(MessageHandler(filters.PHOTO,photo)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text))
 app.run_polling(drop_pending_updates=True)
