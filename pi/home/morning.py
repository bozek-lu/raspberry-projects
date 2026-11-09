import datetime
import json
import os

import bot_stats
import claude_limits
import pi_status
import lotto
import pandora
import weather
from claude_client import ask_claude

FINANCE_PATH = "/home/lbx/server/homepage/public/finance.json"
SPENDING_PATH = "/home/lbx/server/actual/bank-sync/spending.json"
# Not worth suggesting cuts in: family, fixed bills and money moved to Revolut
NO_CUT_CATEGORIES = {"Family & friends", "Housing fees", "Energy, internet & phone", "Health & insurance", "Bank fees", "Revolut", "Uncategorized"}
REMINDERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandora_reminders.json")


def load_reminders():
	try:
		with open(REMINDERS_PATH) as f:
			return json.load(f)
	except FileNotFoundError:
		return []


def save_reminders(reminders):
	with open(REMINDERS_PATH + ".tmp", "w") as f:
		json.dump(reminders, f, indent=1, ensure_ascii=False)
	os.replace(REMINDERS_PATH + ".tmp", REMINDERS_PATH)


def parse_date(word):
	# Accepts "dziś", "jutro", "DD.MM" or "YYYY-MM-DD"; returns None if it is not a date
	today = datetime.date.today()
	word = word.lower()
	if word in ("dziś", "dzis", "dzisiaj"):
		return today
	if word == "jutro":
		return today + datetime.timedelta(days=1)
	try:
		return datetime.date.fromisoformat(word)
	except ValueError:
		pass
	try:
		day, month = (int(x) for x in word.split("."))
		date = datetime.date(today.year, month, day)
		return date if date >= today else date.replace(year=today.year + 1)
	except ValueError:
		return None


def add_reminder(date, text):
	reminders = load_reminders()
	reminders.append({"date": date.isoformat(), "text": text})
	reminders.sort(key=lambda r: r["date"])
	save_reminders(reminders)


def tip():
	today = datetime.date.today().strftime("%A, %d %B %Y")
	return ask_claude(
		f"Dziś jest {today}. Napisz jedną krótką (max 3 zdania) ciekawostkę, praktyczną poradę "
		"albo pomysł na dziś. Za każdym razem inny temat: technika, nauka, zdrowie, jedzenie, "
		"historia tego dnia, Raspberry Pi. Bez wstępów, zwykły tekst bez Markdownu.",
		pandora.system_prompt(),
	)


def budget():
	# Numbers from Actual Budget, written by ~/server/actual/bank-sync/stats.sh after the 6:30 bank sync
	try:
		with open(FINANCE_PATH) as f:
			b = json.load(f)
	except (FileNotFoundError, ValueError):
		return None
	text = (
		f"💰 Budżet (konto osobiste) w tym miesiącu: wydane {b['month_out']}, wpływy {b['month_in']}, odłożone {b['month_saved']}"
		f"\n📉 Poprzedni miesiąc: wydane {b['last_month_out']} · średnio {b['avg_out_3m']}/mies."
		f"\n🏦 Saldo: {b['balance']}"
		f"\n📈 Najwięcej: {b['top_month']}"
	)
	if b["uncategorized"] != "0":
		text += f"\n🏷️ Bez kategorii: {b['uncategorized']}"
	return text


def savings_tip():
	try:
		with open(SPENDING_PATH) as f:
			spending = json.load(f)
	except (FileNotFoundError, ValueError):
		return None
	spending["categories"] = [c for c in spending["categories"] if c["category"] not in NO_CUT_CATEGORIES]
	today = datetime.date.today().strftime("%A, %d %B %Y")
	return ask_claude(
		f"Dziś jest {today}. Oto moje wydatki z konta osobistego w złotych (ten miesiąc do dziś, poprzedni miesiąc, "
		f"średnia z 3 pełnych miesięcy, najwięksi odbiorcy z 4 miesięcy):\n{json.dumps(spending, ensure_ascii=False)}\n\n"
		"Zaproponuj JEDNĄ konkretną rzecz, na której mogę zaoszczędzić, z kwotą miesięcznie (oszczędności najlepiej wydać na lepsze jedzenie dla ciebie, możesz to zasugerować). Wybieraj codziennie inny "
		"temat: kategorię rosnącą względem średniej, powtarzające się małe wydatki, subskrypcje, dostawy jedzenia, loterię. "
		"Nigdy nie proponuj rezygnacji z narzędzi ani subskrypcji AI (Claude, T3 Chat, ChatGPT i podobnych). "
		"Max 2 zdania, bez wstępów i bez moralizowania, zwykły tekst bez Markdownu. Nazwy kategorii po polsku.",
		pandora.system_prompt(),
	)


def build_message():
	today = datetime.date.today()
	reminders = load_reminders()
	due = [r for r in reminders if r["date"] <= today.isoformat()]
	lines = [pandora.greeting(), "", weather.message()]
	if due:
		lines += ["", "📌 Miałam ci przypomnieć:"] + [f"• {r['text']}" for r in due]
		save_reminders([r for r in reminders if r not in due])
	idea = tip()
	if idea:
		lines += ["", f"💡 {idea}"]
	s = pi_status.status()
	pi = f"🍓 Mój ciepły kamyk (Pi): {s['temp']}, zasilanie {s['power']}, kontenery {s['containers']}"
	if s["stopped"]:
		pi += f" (zatrzymane: {', '.join(s['stopped'])})"
	calls, tokens, cost = bot_stats.claude_usage(today - datetime.timedelta(days=1))
	tokens = f"{tokens:,}".replace(",", " ")
	usage = f"🤖 Claude wczoraj: {calls} zapytań, {tokens} tokenów (≈${cost:.2f} w cenach API)"
	limits = claude_limits.summary()
	if limits["claude_week_left"] != "-":
		usage += (
			f"\n📊 Limit tygodniowy: zostało {limits['claude_week_left']} (reset {limits['claude_week_reset']}), "
			f"sesja: zostało {limits['claude_5h_left']}"
			f"\n💻 Claude na Macu: dziś {limits['claude_mac_today']}, 30 dni {limits['claude_mac_30d']}"
		)
	money = budget()
	if money:
		lines += ["", money]
		cut = savings_tip()
		if cut:
			lines.append(f"✂️ {cut}")
	results = lotto.message()
	if results:
		lines += ["", results]
	lines += ["", pi, usage]
	return "\n".join(lines)
