import asyncio
import datetime
import logging
import os
import random
import time
from echo_handler import *
import pi_status
import morning
import bot_stats
import lotto
import pandora
import review
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, ExtBot, MessageHandler, filters)

load_dotenv()

logging.basicConfig(
	format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
	level=logging.INFO
)
# httpx logs request URLs, which contain the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)
TELEGRAM_API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")


class CountingBot(ExtBot):
	# Every Bot API send* call goes through _post, so this counts all outgoing messages in one place
	async def _post(self, endpoint, *args, **kwargs):
		result = await super()._post(endpoint, *args, **kwargs)
		if endpoint.startswith("send") and endpoint != "sendChatAction":
			bot_stats.record_sent()
		return result

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_allowed(update):
		await request_access(update, context)
		return
	await context.bot.send_message(chat_id=update.effective_chat.id, text="Mrrr? Tu Dorcia. Nie wołaj mnie, sama przyjdę, jak zechcę. Co tam?")

async def access_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
	query = update.callback_query
	if not is_owner(update):
		await query.answer(pandora.NO_ACCESS)
		return
	action, user_id = query.data.split(":")
	name = access.name_of(user_id)
	if action == "accept":
		access.set_state(user_id, name, "allowed")
		await query.edit_message_text(f"✅ {name} ma dostęp.")
		await context.bot.send_message(user_id, "Mój człowiek mówi, że jesteś w porządku. Napisz /help, pokażę ci, co umiem (jak będę miała ochotę).")
	elif action == "reject":
		access.set_state(user_id, name, "rejected")
		await query.edit_message_text(f"❌ Odrzucono: {name}")
	elif action == "remove":
		access.set_state(user_id, name, None)
		await query.edit_message_text(f"Usunięto: {name}")
	await query.answer()

async def send_morning(context: ContextTypes.DEFAULT_TYPE):
	owner = os.getenv("TELEGRAM_OWNER_ID")
	text = await asyncio.to_thread(morning.build_message)
	await context.bot.send_message(owner, text)
	bot_stats.record_morning()
	# Now and then a photo of herself, because she can
	if random.random() < 0.3:
		with open(pandora.random_photo(), "rb") as photo:
			await context.bot.send_photo(owner, photo)
	await send_review(context, owner)

async def send_review(context: ContextTypes.DEFAULT_TYPE, chat_id, when_empty=None):
	try:
		cats, items = await asyncio.to_thread(review.pending)
	except Exception as e:
		logging.error(f"Spending review failed: {e}")
		if when_empty:
			await context.bot.send_message(chat_id, "Nie mogę dosięgnąć budżetu, ktoś zamknął drzwi. Spróbuję później.")
		return
	if not items:
		if when_empty:
			await context.bot.send_message(chat_id, when_empty)
		return
	await context.bot.send_message(chat_id,
		f"🧾 Przejrzałam twoje wydatki ({len(items)}). Za każdy mogłeś mi kupić lepszą karmę, ale trudno. "
		"Dobrze je poukładałam?")
	for tx in items:
		text, buttons = review.question(tx, cats)
		await context.bot.send_message(chat_id, text, reply_markup=buttons)

async def review_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	await send_review(context, update.effective_chat.id, when_empty="Wszystko poukładane. Mogę wracać do drzemki. 😴")

async def review_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
	query = update.callback_query
	if not is_owner(update):
		await query.answer(pandora.NO_ACCESS)
		return
	_, action, tx_id, *rest = query.data.split(":")
	await query.answer()
	tx = await asyncio.to_thread(review.transaction, tx_id)
	if not tx:
		await query.edit_message_text("Tego wydatku już nie widzę. Może go zjadłam.")
		return
	if action == "ok":
		review.mark_reviewed(tx["id"], tx["date"])
		await query.edit_message_text(f"{review.describe(tx)}\n✅ {tx['category']}")
	elif action == "ch":
		cats = await asyncio.to_thread(review.categories)
		await query.edit_message_reply_markup(review.category_picker(tx["id"], cats))
	elif action in ("set", "rem"):
		cats = await asyncio.to_thread(review.categories)
		index = int(rest[0])
		cat = cats[index]
		await asyncio.to_thread(review.categorize, tx, cat["id"], action == "rem")
		review.mark_reviewed(tx["id"], tx["date"])
		if action == "rem":
			await query.edit_message_text(f"{review.describe(tx)}\n✅ {cat['name']} (zapamiętam dla {tx['payee']})")
		else:
			await query.edit_message_text(f"{review.describe(tx)}\n✅ {cat['name']}", reply_markup=review.remember_button(tx, index))

async def morning_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	await send_morning(context)

async def lotto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	args = context.args
	if args and args[0] in ("dodaj", "add"):
		numbers = lotto.parse_numbers(args[1:])
		if not numbers:
			await update.message.reply_text("Potrzebuję sześciu różnych liczb od 1 do 49, np. /lotto dodaj 3 7 12 25 33 41")
			return
		sets = lotto.my_numbers() + [numbers]
		lotto.save_numbers(sets)
		await update.message.reply_text(f"🎟️ Zapamiętałam zestaw {len(sets)}: {' '.join(map(str, numbers))}. Rano sprawdzę, czy stać cię na lepszą karmę.")
		return
	if args and args[0] in ("usuń", "usun", "clear"):
		lotto.save_numbers([])
		await update.message.reply_text("Zapomniałam twoje liczby. I tak było mi wszystko jedno.")
		return
	text = await asyncio.to_thread(lotto.message)
	await update.message.reply_text(text or "Nie mogę znaleźć wyników, ktoś schował kulki pod kanapę.")

async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	date = morning.parse_date(context.args[0]) if context.args else None
	text = " ".join(context.args[1:])
	if not date or not text:
		await update.message.reply_text("Powiedz mi tak: /remind jutro kupić mi lepszą karmę\nData: dziś, jutro, 25.12 albo 2026-12-25")
		return
	morning.add_reminder(date, text)
	await update.message.reply_text(f"📌 Zapamiętam: {date.strftime('%d.%m.%Y')} rano – {text}. Chyba że zasnę, ale i tak cię obudzę.")

async def reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	items = morning.load_reminders()
	if not items:
		await update.message.reply_text("Nic nie mam do zapamiętania. Pamiętam tylko o kolacji. Dodaj: /remind jutro coś ważnego")
		return
	await update.message.reply_text("\n".join(f"{r['date']}: {r['text']}" for r in items))

async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	data = access.load()
	if not any(data.values()):
		await update.message.reply_text("Nikt jeszcze nie chciał się ze mną zaprzyjaźnić.")
		return
	labels = {"allowed": "✅", "pending": "⏳", "rejected": "❌"}
	for key, label in labels.items():
		for user_id, name in data[key].items():
			buttons = [InlineKeyboardButton("Usuń", callback_data=f"remove:{user_id}")]
			if key != "allowed":
				buttons.insert(0, InlineKeyboardButton("Akceptuj", callback_data=f"accept:{user_id}"))
			await update.message.reply_text(f"{label} {name}", reply_markup=InlineKeyboardMarkup([buttons]))

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
	await update.message.reply_text(f"Twoje ID: {update.effective_user.id}")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
	text = (
		"Mrrr. Oto, co umiem (kiedy mi się chce):\n\n"
		"roll – rzut kostką\n"
		"pandorka – losowe zdjęcie Pandory\n"
		"/whoami – twoje ID"
	)
	if is_owner(update):
		text += (
			"\n\nTylko dla właściciela:\n"
			"zwykła wiadomość albo ?pytanie – pogadaj ze mną\n"
			"!komenda – polecenie na Raspberry Pi\n"
			"/status – stan Raspberry Pi\n"
			"/dashboard – linki do panelu\n"
			"/users – kto ma dostęp do Pandory\n"
			"/remind jutro tekst – przypomnienie rano (dziś, jutro, 25.12)\n"
			"/reminders – lista przypomnień\n"
			"/morning – poranna wiadomość teraz\n"
			"/review – sprawdź kategorie ostatnich wydatków\n"
			"/lotto – ostatnie losowanie (dodaj 1 2 3 4 5 6 – twoje liczby, usuń – zapomnij je)"
		)
	await update.message.reply_text(text)

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	lines = [f"{name}: {url}" for name, url in pi_status.links().items()]
	await update.message.reply_text("\n".join(lines) + "\n\n(działa z urządzeń w Tailscale)", disable_web_page_preview=True)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	s = pi_status.status()
	text = (
		f"🍓 Mój ciepły kamyk:\n"
		f"Temperatura: {s['temp']}\n"
		f"Zasilanie: {s['power']}\n"
		f"Uptime: {s['uptime']}\n"
		f"Wolne miejsce: {s['disk_free']}\n"
		f"Kontenery: {s['containers']}"
	)
	if s['stopped']:
		text += f"\nZatrzymane: {', '.join(s['stopped'])}"
	await update.message.reply_text(text)

async def count(update: Update, context: ContextTypes.DEFAULT_TYPE):
	bot_stats.record(update.effective_user.id, update.message.text)

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
	print(update.message.text.encode('utf-8'))
	await handle_echo(update, context)


if __name__ == '__main__':
	application = ApplicationBuilder().bot(CountingBot(TELEGRAM_API_TOKEN)).build()

	pandora_filter = PandoraFilter()

	# Group -1 sees every text message before the handlers below
	application.add_handler(MessageHandler(filters.TEXT, count), group=-1)
	start_handler = CommandHandler('start', start)
	echo_handler = MessageHandler(pandora_filter & (~filters.COMMAND), echo)
	application.add_handler(start_handler)
	application.add_handler(CommandHandler('whoami', whoami))
	application.add_handler(CommandHandler('help', help))
	application.add_handler(CommandHandler('users', users))
	application.add_handler(CommandHandler('remind', remind))
	application.add_handler(CommandHandler('reminders', reminders))
	application.add_handler(CommandHandler('morning', morning_now))
	application.add_handler(CommandHandler('review', review_now))
	application.add_handler(CommandHandler('lotto', lotto_command))
	application.add_handler(CallbackQueryHandler(review_button, pattern=r"^rv:"))
	application.job_queue.run_daily(send_morning, time=datetime.time(7, 30, tzinfo=ZoneInfo("Europe/Warsaw")))
	application.add_handler(CallbackQueryHandler(access_decision, pattern=r"^(accept|reject|remove):\d+$"))
	application.add_handler(CommandHandler('dashboard', dashboard))
	application.add_handler(CommandHandler('status', status))
	application.add_handler(echo_handler)

	application.run_polling()