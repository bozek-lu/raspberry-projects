import asyncio
import datetime
import html
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
import receipts
import banksync
import piano
import tempfile
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
# The scheduler logs every run of the once-a-minute piano song check
logging.getLogger("apscheduler").setLevel(logging.WARNING)
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
	await context.bot.send_message(owner, text, disable_web_page_preview=True)
	bot_stats.record_morning()
	# Now and then a photo of herself, because she can
	if random.random() < 0.3:
		with open(pandora.random_photo(), "rb") as photo:
			await context.bot.send_photo(owner, photo)
	await send_review(context, owner)
	await retry_receipts(context, owner)

# Caption that sends a photo straight to Pandora's album instead of Paperless
CAT_CAPTIONS = ("kot", "pandora", "pandorka", "🐱", "🐈")

def photo_choice(message_id):
	return InlineKeyboardMarkup([[
		InlineKeyboardButton("🧾 Paragon", callback_data=f"ph:doc:{message_id}"),
		InlineKeyboardButton("🐱 Pandorka", callback_data=f"ph:cat:{message_id}"),
	]])

# Telegram's default 5 s is too short for fetching photos from a Pi now and then
FILE_TIMEOUT = 60

async def download(context: ContextTypes.DEFAULT_TYPE, file_id, path):
	file = await context.bot.get_file(file_id, read_timeout=FILE_TIMEOUT)
	await file.download_to_drive(path, read_timeout=FILE_TIMEOUT)

async def save_cat_photo(context: ContextTypes.DEFAULT_TYPE, file_id):
	name = datetime.datetime.now().strftime("pandorka-%Y%m%d-%H%M%S.jpg")
	try:
		await download(context, file_id, os.path.join(pandora.PHOTO_DIR, name))
	except Exception as e:
		logging.error(f"Saving cat photo failed: {e}")
		return "Telegram nie oddał mi zdjęcia. Wyślij je jeszcze raz."
	return random.choice(pandora.PHOTO_SAVED)

async def photo_or_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
	# Owner only; other people's pictures are ignored as before.
	# Files and PDFs go to Paperless. Photos: the caption decides, or Pandora asks.
	if not is_owner(update):
		return
	message = update.message
	if message.photo:
		file_id = message.photo[-1].file_id
		caption = (message.caption or "").strip()
		first_word = caption.lower().split()[0] if caption else ""
		if first_word in CAT_CAPTIONS or first_word.startswith(("🐱", "🐈")):
			await message.reply_text(await save_cat_photo(context, file_id), reply_to_message_id=message.message_id)
		elif caption:
			await start_receipt(context, message, file_id, f"paragon-{datetime.date.today().isoformat()}.jpg", caption)
		else:
			context.chat_data.setdefault("photos", {})[str(message.message_id)] = file_id
			await message.reply_text("📷 Co to jest?", reply_markup=photo_choice(message.message_id), reply_to_message_id=message.message_id)
		return
	doc = message.document
	if doc.file_size and doc.file_size > 20 * 1024 * 1024:
		await message.reply_text("Za ciężkie, Telegram pozwala mi unieść najwyżej 20 MB.")
		return
	# MIDI files go to the keyboard's songs, audio files to the piano queue
	kind = piano.kind_of(doc.file_name, doc.mime_type)
	if kind:
		await piano_file(context, message, doc.file_id, doc.file_name, kind, ".mid" if kind == "midi" else ".mp3")
		return
	if not (doc.mime_type == "application/pdf" or (doc.mime_type or "").startswith("image/")):
		await message.reply_text("Biorę zdjęcia i PDF-y (paragony) oraz MIDI i pliki audio (keyboard). Resztę zrzucam ze stołu.")
		return
	await start_receipt(context, message, doc.file_id, doc.file_name or "dokument", message.caption)

async def start_receipt(context: ContextTypes.DEFAULT_TYPE, message, file_id, name, title):
	fd, path = tempfile.mkstemp(suffix=os.path.splitext(name)[1])
	os.close(fd)
	try:
		await download(context, file_id, path)
	except Exception as e:
		logging.error(f"Receipt download failed: {e}")
		os.remove(path)
		await message.reply_text("❌ Telegram nie oddał mi pliku. Wyślij go jeszcze raz.", reply_to_message_id=message.message_id)
		return
	await message.reply_text("📥 Mam! Paperless czyta, to potrwa minutę albo dwie.", reply_to_message_id=message.message_id)
	context.application.create_task(process_receipt(context, message.chat_id, message.message_id, path, name, title))

async def photo_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
	query = update.callback_query
	if not is_owner(update):
		await query.answer(pandora.NO_ACCESS)
		return
	_, kind, message_id = query.data.split(":")
	await query.answer()
	file_id = context.chat_data.get("photos", {}).pop(message_id, None)
	if not file_id:
		await query.edit_message_text("Zgubiłam to zdjęcie (pewnie po drzemce). Wyślij je jeszcze raz.")
		return
	if kind == "cat":
		await query.edit_message_text(await save_cat_photo(context, file_id))
	else:
		await query.edit_message_text("🧾 Paragon, biorę.")
		await start_receipt(context, query.message.reply_to_message or query.message, file_id,
			f"paragon-{datetime.date.today().isoformat()}.jpg", None)

async def process_receipt(context: ContextTypes.DEFAULT_TYPE, chat_id, reply_to, path, name, title):
	async def say(text, **kwargs):
		await context.bot.send_message(chat_id, text, reply_to_message_id=reply_to, disable_web_page_preview=True, **kwargs)
	try:
		task_id = await asyncio.to_thread(receipts.upload, path, name, title)
		doc_id, error = await asyncio.to_thread(receipts.wait_for_document, task_id)
	except Exception as e:
		logging.error(f"Paperless upload failed: {e}")
		await say("❌ Nie mogę dosięgnąć Paperless. Ktoś zamknął drzwi?")
		return
	finally:
		os.remove(path)
	if error:
		await say(f"❌ Paperless go nie przyjął: {error}")
		return
	try:
		doc = await asyncio.to_thread(receipts.document, doc_id)
		info = await asyncio.to_thread(receipts.extract, doc.get("content"))
	except Exception as e:
		logging.error(f"Receipt reading failed: {e}")
		doc, info = {"id": doc_id}, None
	receipts.log_receipt(doc_id, info, "unread")

	# Exactly the same file sent before: Paperless keeps both, so ask what to do
	earlier = receipts.duplicates(doc)
	if earlier:
		receipts.set_status(doc_id, "duplicate")
		first = earlier[0]
		await say(f"🔁 Ten sam plik już jest w Paperless: #{first['id']} {first.get('title', '')}\n{receipts.link(first['id'])}\nCo z nowym?",
			reply_markup=receipts.duplicate_buttons(doc_id))
		return

	if not receipts.matchable(info):
		await say(f"🗂️ Schowane w Paperless: {receipts.link(doc_id)}\nNie znalazłam kwoty i daty, więc nie szukam w budżecie.")
		return
	if not title:
		try:
			await asyncio.to_thread(receipts.rename, doc_id, info)
		except Exception as e:
			logging.error(f"Paperless rename failed: {e}")
	await say(f"🗂️ {receipts.describe(info)}\n{receipts.link(doc_id)}")
	await first_match(context, chat_id, doc_id, info)

async def first_match(context: ContextTypes.DEFAULT_TYPE, chat_id, doc_id, info):
	# Nothing in the budget yet: ask the bank for fresh transactions (rate-limited), then look again
	result = await match_receipt(context, chat_id, doc_id, info)
	if result != "none":
		return
	ok, reason = banksync.allowed()
	if not ok:
		receipts.remember(doc_id, info)
		await context.bot.send_message(chat_id, f"⏳ W budżecie jeszcze nie ma {receipts.describe(info)}. Nie pytam banku ({reason}). Sprawdzę rano.")
		return
	await context.bot.send_message(chat_id, f"🏦 W budżecie jeszcze nie ma {receipts.describe(info)}. Pytam bank o świeże wyciągi…")
	synced = await asyncio.to_thread(banksync.run)
	if synced:
		result = await match_receipt(context, chat_id, doc_id, info)
	if result == "none":
		receipts.remember(doc_id, info)
		why = "Bank jeszcze go nie pokazuje (płatności kartą wchodzą zwykle po 1–2 dniach)." if synced else "Bank nie odpowiedział."
		await context.bot.send_message(chat_id, f"⏳ {why} Sprawdzę rano.")
	if synced:
		# The fresh transactions may also fit receipts that were already waiting
		await retry_receipts(context, chat_id, skip=doc_id)

async def match_receipt(context: ContextTypes.DEFAULT_TYPE, chat_id, doc_id, info):
	"""Link the receipt if its spending is in Actual. Returns linked / choose / duplicate / none."""
	try:
		free, taken = await asyncio.to_thread(receipts.candidates, info)
	except Exception as e:
		logging.error(f"Receipt match failed: {e}")
		return "none"
	if len(free) == 1:
		await asyncio.to_thread(receipts.attach, doc_id, free[0])
		await context.bot.send_message(chat_id, f"🔗 Paragon {receipts.describe(info)} przypięty w budżecie do: {receipts.describe_tx(free[0])}")
		return "linked"
	if free:
		receipts.remember(doc_id, info)
		await context.bot.send_message(chat_id, f"🤔 {receipts.describe(info)} pasuje do kilku wydatków. Który to?",
			reply_markup=receipts.picker(doc_id, free))
		return "choose"
	if taken:
		# The only matching spending already has a receipt: most likely the same receipt again
		receipts.forget(doc_id)
		receipts.set_status(doc_id, "duplicate")
		earlier = receipts.receipt_of(taken[0])
		await context.bot.send_message(chat_id,
			f"🔁 {receipts.describe(info)}: ten wydatek ({receipts.describe_tx(taken[0])}) ma już paragon #{earlier}\n{receipts.link(earlier)}\nTo chyba ten sam. Co z nowym?",
			reply_markup=receipts.duplicate_buttons(doc_id), disable_web_page_preview=True)
		return "duplicate"
	return "none"

async def retry_receipts(context: ContextTypes.DEFAULT_TYPE, chat_id, skip=None):
	for doc_id, info in receipts.pending().items():
		if doc_id == skip:
			continue
		if receipts.expired(info):
			receipts.forget(doc_id)
			receipts.set_status(doc_id, "unmatched")
			await context.bot.send_message(chat_id, f"🤷 Przez {receipts.RETRY_DAYS} dni nie znalazłam w budżecie {receipts.describe(info)}. Zostaje tylko w Paperless: {receipts.link(doc_id)}",
				disable_web_page_preview=True)
		else:
			await match_receipt(context, chat_id, doc_id, info)
	try:
		await asyncio.to_thread(receipts.write_tile)
	except Exception as e:
		logging.error(f"Receipts tile failed: {e}")

async def duplicate_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
	query = update.callback_query
	if not is_owner(update):
		await query.answer(pandora.NO_ACCESS)
		return
	_, doc_id, action = query.data.split(":")
	await query.answer()
	if action == "del":
		try:
			await asyncio.to_thread(receipts.delete, int(doc_id))
		except Exception as e:
			logging.error(f"Paperless delete failed: {e}")
			await query.edit_message_text("Nie udało się usunąć, Paperless się zaparł. Usuń ręcznie: " + receipts.link(doc_id))
			return
		await query.edit_message_text(f"🗑️ Usunęłam nowy #{doc_id}. Leży w koszu Paperless przez 30 dni, gdybyś żałował.")
	else:
		await query.edit_message_text(f"📚 Zostawiam oba. Nowy to #{doc_id}.")

async def receipt_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
	query = update.callback_query
	if not is_owner(update):
		await query.answer(pandora.NO_ACCESS)
		return
	_, doc_id, tx_id = query.data.split(":", 2)
	await query.answer()
	info = receipts.pending_info(doc_id)
	if not info:
		await query.edit_message_text("Ten paragon już załatwiony.")
		return
	if tx_id == "none":
		await query.edit_message_text(f"{receipts.describe(info)}: dobra, poszukam jeszcze rano.")
		return
	free, _ = await asyncio.to_thread(receipts.candidates, info)
	tx = next((t for t in free if t["id"] == tx_id), None)
	if not tx:
		await query.edit_message_text("Tego wydatku już nie widzę albo ma już paragon.")
		return
	await asyncio.to_thread(receipts.attach, int(doc_id), tx)
	await query.edit_message_text(f"🔗 Paragon {receipts.describe(info)} przypięty do: {receipts.describe_tx(tx)}")

# --- Piano songs for the keyboard (EZ-310 + Tab5) ---
# One conversion at a time: each one keeps three Pi cores busy
PIANO_LOCK = asyncio.Lock()
# Where the computer converter lives (HTML: tap a command in Telegram to copy it)
PC_HINT = (
	"💻 <b>Nuty na komputerze</b>: skrypt <code>tools/piano-convert.py</code> w repo raspberry-projects.\n\n"
	"<b>Mac</b>\n"
	"<code>cd ~/raspberry-projects &amp;&amp; git pull &amp;&amp; python3 tools/piano-convert.py</code>\n"
	"Pierwszy raz (pobiera repo):\n"
	"<code>git clone https://github.com/bozek-lu/raspberry-projects.git ~/raspberry-projects</code>\n\n"
	"<b>Windows</b> (PowerShell)\n"
	"<code>cd ~\\claude\\raspberry-projects; git pull; python tools\\piano-convert.py</code>\n\n"
	"Pierwsze uruchomienie instaluje ok. 1 GB (torch) i pobiera model. Potrzebny Python 3.10+ i ssh do mnie. "
	"Gotowe nuty sama ci wyślę.")

def piano_choice(entry):
	qid, seconds = entry["id"], entry.get("seconds")
	return InlineKeyboardMarkup([
		[InlineKeyboardButton(f"⚡ Szybko tutaj (~{piano.estimate(seconds, True)} min, mniej dokładnie)", callback_data=f"pn:fast:{qid}")],
		[InlineKeyboardButton(f"🎯 Dokładnie tutaj (~{piano.estimate(seconds, False)} min)", callback_data=f"pn:acc:{qid}")],
		[InlineKeyboardButton("💻 Na komputerze", callback_data=f"pn:pc:{qid}"),
		 InlineKeyboardButton("🗑️ Usuń", callback_data=f"pn:del:{qid}")],
	])

async def youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
	message = update.message
	await message.reply_text("📥 Pobieram dźwięk…", reply_to_message_id=message.message_id)
	context.application.create_task(fetch_and_ask(context, message.chat_id, message.message_id,
		piano.fetch_link, piano.find_link(message.text)))

async def fetch_and_ask(context: ContextTypes.DEFAULT_TYPE, chat_id, reply_to, fetch, *args):
	"""Put the audio in the queue, then ask where to turn it into notes."""
	try:
		entry = await asyncio.to_thread(fetch, *args)
	except Exception as e:
		logging.error(f"Piano fetch failed: {e}")
		entry = {"ok": False, "error": str(e)}
	if not entry.get("ok"):
		await context.bot.send_message(chat_id, f"❌ Nie wyszło: {entry.get('error')}", reply_to_message_id=reply_to)
		return
	await context.bot.send_message(chat_id, f"🎵 {piano.describe(entry)} czeka w kolejce. Jak spisać nuty?",
		reply_markup=piano_choice(entry), reply_to_message_id=reply_to)

async def piano_file(context: ContextTypes.DEFAULT_TYPE, message, file_id, file_name, kind, default_ext):
	"""A MIDI file goes to the songs; an audio file goes to the queue."""
	path = piano.incoming_path(file_name, default_ext)
	try:
		await download(context, file_id, path)
	except Exception as e:
		logging.error(f"Piano file download failed: {e}")
		await message.reply_text("❌ Telegram nie oddał mi pliku. Wyślij go jeszcze raz.", reply_to_message_id=message.message_id)
		return
	title = (message.caption or "").strip() or os.path.splitext(file_name or "")[0] or "piosenka"
	if kind == "midi":
		result = await asyncio.to_thread(piano.add_midi, path, title)
		if result.get("ok"):
			channels = ", ".join(map(str, result.get("channels", []))) or "brak"
			await message.reply_text(f"🎹 Dodane do piosenek: {piano.describe(result)}\nKanały z nutami: {channels} "
				"(światła EZ-310: 1 = prawa ręka, 2 = lewa).", reply_to_message_id=message.message_id)
		else:
			await message.reply_text(f"❌ Nie wyszło: {result.get('error')}", reply_to_message_id=message.message_id)
		return
	await message.reply_text("📥 Mam, wkładam do kolejki…", reply_to_message_id=message.message_id)
	context.application.create_task(fetch_and_ask(context, message.chat_id, message.message_id, piano.fetch_file, path, title))

async def audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
	message = update.message
	audio = message.audio or message.voice
	name = getattr(audio, "file_name", None) or (f"{audio.title}.ogg" if getattr(audio, "title", None) else None)
	if audio.file_size and audio.file_size > 20 * 1024 * 1024:
		await message.reply_text("Za ciężkie, Telegram pozwala mi unieść najwyżej 20 MB. Wyślij link do YouTube.")
		return
	await piano_file(context, message, audio.file_id, name, "audio", ".ogg")

async def piano_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
	query = update.callback_query
	if not is_owner(update):
		await query.answer(pandora.NO_ACCESS)
		return
	_, action, qid = query.data.split(":", 2)
	await query.answer()
	entry = next((e for e in piano.queue() if e["id"] == qid), None)
	if not entry:
		await query.edit_message_text("Tego już nie ma w kolejce (pewnie gotowe albo usunięte). /piano pokaże piosenki.")
		return
	if action == "del":
		await asyncio.to_thread(piano.drop, qid)
		await query.edit_message_text(f"🗑️ Usunięte z kolejki: {piano.describe(entry)}")
	elif action == "pc":
		await query.edit_message_text(f"⏳ {html.escape(piano.describe(entry))} zostaje w kolejce.\n\n{PC_HINT}",
			parse_mode="HTML", disable_web_page_preview=True)
	else:
		fast = action == "fast"
		waiting = " Najpierw kończę poprzednią." if PIANO_LOCK.locked() else ""
		await query.edit_message_text(f"⏳ Spisuję nuty: {piano.describe(entry)}, "
			f"{'szybko' if fast else 'dokładnie'}, ~{piano.estimate(entry.get('seconds'), fast)} min.{waiting}")
		context.application.create_task(convert_and_send(context, query.message.chat_id, query.message.message_id, entry, fast))

async def convert_and_send(context: ContextTypes.DEFAULT_TYPE, chat_id, reply_to, entry, fast):
	async with PIANO_LOCK:
		try:
			result = await asyncio.to_thread(piano.convert, entry["id"], fast)
		except Exception as e:
			logging.error(f"Piano conversion failed: {e}")
			result = {"ok": False, "error": str(e)}
	if not result.get("ok"):
		await context.bot.send_message(chat_id, f"❌ Nie wyszło: {result.get('error')}\nPlik zostaje w kolejce (/piano kolejka).",
			reply_to_message_id=reply_to)
		return
	context.bot_data.setdefault("piano_seen", set()).add(song_key(result))
	await send_song(context, chat_id, result, reply_to)

def song_key(song):
	# A song converted again (e.g. accurate on a computer after a fast draft) keeps its file name
	return f"{song['file']}@{song.get('added')}"

async def send_song(context: ContextTypes.DEFAULT_TYPE, chat_id, song, reply_to=None):
	draft = "\n⚡ Szybka wersja: mogą być pomyłki, dokładną zrobisz na komputerze." if song.get("source") == "pi-fast" else ""
	caption = (f"🎹 {piano.describe(song)}\n"
		f"Prawa ręka na kanale 1, lewa na 2 (światła EZ-310). Tab5 znajdzie ją na liście piosenek.{draft}")
	try:
		with open(piano.song_path(song), "rb") as f:
			await context.bot.send_document(chat_id, f, filename=song["file"], caption=caption,
				reply_to_message_id=reply_to, write_timeout=FILE_TIMEOUT)
	except Exception as e:
		logging.error(f"Sending MIDI failed: {e}")
		await context.bot.send_message(chat_id, caption + f"\n(plik: {piano.SONGS_URL}/{song['file']})", reply_to_message_id=reply_to)

async def announce_new_songs(context: ContextTypes.DEFAULT_TYPE):
	"""Songs converted on a computer (tools/piano-convert.py) appear in songs.json: send them over."""
	seen = context.bot_data.get("piano_seen")
	current = piano.songs()
	if seen is None:
		context.bot_data["piano_seen"] = {song_key(s) for s in current}
		return
	for song in current:
		if song_key(song) not in seen:
			seen.add(song_key(song))
			if song.get("source") == "computer":
				await send_song(context, os.getenv("TELEGRAM_OWNER_ID"), song)

async def piano_songs(update: Update, context: ContextTypes.DEFAULT_TYPE):
	if not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS)
		return
	if context.args and context.args[0].lower() in ("komputer", "pc", "mac"):
		await update.message.reply_text(PC_HINT, parse_mode="HTML", disable_web_page_preview=True)
		return
	if context.args and context.args[0].lower() in ("kolejka", "queue", "k"):
		waiting = piano.queue()
		if not waiting:
			await update.message.reply_text("Kolejka pusta. Wyślij link do YouTube albo plik audio.")
			return
		await update.message.reply_text(f"⏳ W kolejce do spisania nut: {len(waiting)}\n\n{PC_HINT}",
			parse_mode="HTML", disable_web_page_preview=True)
		for entry in waiting:
			await update.message.reply_text(f"🎵 {piano.describe(entry)}", reply_markup=piano_choice(entry))
		return
	songs = piano.songs()
	if not songs:
		await update.message.reply_text("Jeszcze żadnych piosenek. Wyślij mi link do YouTube, plik audio albo MIDI.")
		return
	lines = [f"• {piano.describe(s)}" for s in songs[-15:]]
	queued = len(piano.queue())
	tail = f"\n\nW kolejce: {queued} (/piano kolejka)" if queued else ""
	await update.message.reply_text("🎹 Piosenki dla keyboardu:\n" + "\n".join(lines) + tail + f"\n\n{piano.SONGS_URL}/songs.json",
		disable_web_page_preview=True)

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
			"zdjęcie – zapytam: 🧾 paragon czy 🐱 do albumu (podpis „kot” = od razu do albumu, inny podpis = paragon z tym tytułem)\n"
			"plik albo PDF – prosto do Paperless i do budżetu (najczytelniejsze paragony)\n"
			"/lotto – ostatnie losowanie (dodaj 1 2 3 4 5 6 – twoje liczby, usuń – zapomnij je)\n"
			"link do YouTube albo plik audio – pobiorę do kolejki i spiszę nuty (MIDI) dla keyboardu: szybko, dokładnie albo na komputerze\n"
			"plik MIDI – od razu do piosenek dla keyboardu\n"
			"/piano – lista piosenek, /piano kolejka – co czeka na spisanie nut, /piano komputer – jak spisać nuty na komputerze"
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
	application.add_handler(CallbackQueryHandler(receipt_button, pattern=r"^rc:"))
	application.add_handler(CallbackQueryHandler(duplicate_button, pattern=r"^rd:"))
	application.add_handler(CallbackQueryHandler(photo_button, pattern=r"^ph:"))
	application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, photo_or_document))
	application.job_queue.run_daily(send_morning, time=datetime.time(7, 30, tzinfo=ZoneInfo("Europe/Warsaw")))
	application.add_handler(CallbackQueryHandler(access_decision, pattern=r"^(accept|reject|remove):\d+$"))
	application.add_handler(CommandHandler('dashboard', dashboard))
	application.add_handler(CommandHandler('status', status))
	application.add_handler(CommandHandler('piano', piano_songs))
	# Owner's YouTube links go to the piano transcriber instead of the chat
	owner_filter = filters.User(user_id=int(os.getenv("TELEGRAM_OWNER_ID")))
	application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & owner_filter & filters.Regex(piano.YOUTUBE_RE), youtube_link))
	application.add_handler(MessageHandler((filters.AUDIO | filters.VOICE) & owner_filter, audio_message))
	application.add_handler(CallbackQueryHandler(piano_button, pattern=r"^pn:"))
	application.job_queue.run_repeating(announce_new_songs, interval=60, first=5)
	application.add_handler(echo_handler)

	application.run_polling()