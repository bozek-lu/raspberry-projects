import logging
import os
import getopt, sys
import random
import time
from glob import glob

from telegram import *
from telegram.ext.filters import MessageFilter
from telegram.ext import (ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters)

from claude_client import ask_claude
import access
import pandora
import asyncio

# Last few messages, so Pandora remembers what the chat is about
HISTORY_LENGTH = 6

def is_owner(update: Update):
	return str(update.effective_user.id) == os.getenv("TELEGRAM_OWNER_ID")

def is_allowed(update: Update):
	return is_owner(update) or access.state(update.effective_user.id) == "allowed"

async def request_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
	# Ask the owner once; pending and rejected users are not asked about again
	user = update.effective_user
	if access.state(user.id) is not None:
		return
	name = user.full_name + (f" (@{user.username})" if user.username else "")
	access.set_state(user.id, name, "pending")
	buttons = InlineKeyboardMarkup([[
		InlineKeyboardButton("✅ Akceptuj", callback_data=f"accept:{user.id}"),
		InlineKeyboardButton("❌ Odrzuć", callback_data=f"reject:{user.id}"),
	]])
	await context.bot.send_message(os.getenv("TELEGRAM_OWNER_ID"), f"{name} chce się ze mną zaprzyjaźnić. Wpuszczamy?", reply_markup=buttons)
	await update.message.reply_text("Pójdę miauknąć do mojego człowieka, czy możesz ze mną gadać. Dam znać!")

async def handle_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
	text = update.message.text

	if not is_allowed(update):
		await request_access(update, context)
		return

	# Shell and Claude only for the owner set in .env
	if (text.startswith('!') or text.startswith('?')) and not is_owner(update):
		await update.message.reply_text(pandora.NO_ACCESS, reply_to_message_id=update.message.message_id)
		return

	if text.startswith('!'):
		new_text = text[1:]
		message = run_bash_command(new_text)
		await update.message.reply_text(message, reply_to_message_id=update.message.message_id)
	elif text.lower() == 'roll':
		await context.bot.send_dice(chat_id=update.effective_chat.id, reply_to_message_id=update.message.message_id)
	elif text.lower() == 'pandorka':
		await sendingRandomPandoraPic(update.effective_chat.id, context)
	else:
		question = text[1:] if text.startswith('?') else text
		history = context.chat_data.setdefault("history", [])
		prompt = question
		if history:
			prompt = "Wcześniej w rozmowie:\n" + "\n".join(history) + f"\n\nTeraz Łukasz pisze: {question}"
		await context.bot.send_chat_action(update.effective_chat.id, "typing")
		message = await asyncio.to_thread(ask_claude, prompt, pandora.system_prompt())
		if message:
			history += [f"Łukasz: {question}", f"Ty: {message}"]
			del history[:-HISTORY_LENGTH]
		message = (message or pandora.CLAUDE_DOWN)[:4000]
		await update.message.reply_text(message, reply_to_message_id=update.message.message_id)


async def sendingRandomPandoraPic(chat_id, context):
    pic_list = glob("./pandorka/*")

    get_random_pic = random.choice(pic_list)
    with open(get_random_pic, 'rb') as photo:
       await context.bot.sendPhoto(chat_id, photo)


class PandoraFilter(MessageFilter):
	def filter(self, message):
		text = message.text.lower()
		# Known commands
		if text == 'roll' or text == 'pandorka':
			return True
		else:
			# Claude question or bash script; the owner can also just talk to her
			is_owner = str(message.from_user.id) == os.getenv("TELEGRAM_OWNER_ID")
			return text.startswith('?') or text.startswith('!') or is_owner


def run_bash_command(cmd: str):
	import subprocess
	# out = subprocess.run([cmd], stdout=subprocess.PIPE)
	# result = out.stdout.decode('utf-8')

	try:
		process = subprocess.run(cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
		result = process.stdout.decode('utf-8') + process.stderr.decode('utf-8')
	except Exception as e:
		result = str(e)

	print(result)

	# Telegram rejects empty messages and caps them at 4096 characters
	result = result.replace('\\n', '\n').strip() or "(no output)"
	return result[-4000:]
