import datetime
import json
import os
import urllib.request

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Daily check of new spending on mBank Personal, through the actual-bridge container
BRIDGE = "http://127.0.0.1:5007"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandora_reviewed.json")
LOOKBACK_DAYS = 7


def _get(path):
	with urllib.request.urlopen(BRIDGE + path, timeout=60) as r:
		return json.load(r)


def _post(path, data):
	req = urllib.request.Request(BRIDGE + path, json.dumps(data).encode(), {"content-type": "application/json"})
	with urllib.request.urlopen(req, timeout=60) as r:
		return json.load(r)


def _load_state():
	try:
		with open(STATE_PATH) as f:
			return json.load(f)
	except FileNotFoundError:
		return {}


def _save_state(state):
	# Keep only recent ids; older transactions are outside the lookback anyway
	cutoff = (datetime.date.today() - datetime.timedelta(days=LOOKBACK_DAYS + 7)).isoformat()
	state = {k: v for k, v in state.items() if v >= cutoff}
	with open(STATE_PATH + ".tmp", "w") as f:
		json.dump(state, f)
	os.replace(STATE_PATH + ".tmp", STATE_PATH)


def mark_reviewed(tx_id, date):
	state = _load_state()
	state[tx_id] = date
	_save_state(state)


def pending():
	# Spending not yet confirmed; bank card payments often appear a day or two late, so look back a week
	data = _get(f"/review?days={LOOKBACK_DAYS}")
	done = _load_state()
	return data["categories"], [t for t in data["transactions"] if t["id"] not in done]


def categories():
	return _get(f"/review?days=0")["categories"]


def transaction(tx_id):
	return next((t for t in _get(f"/review?days={LOOKBACK_DAYS}")["transactions"] if t["id"] == tx_id), None)


def categorize(tx, category_id, remember=False):
	_post("/categorize", {"id": tx["id"], "category_id": category_id, "payee_id": tx["payee_id"], "remember": remember})


def describe(tx):
	date = datetime.date.fromisoformat(tx["date"]).strftime("%d.%m")
	amount = f"{tx['amount_zl']:,.2f}".replace(",", " ").replace(".", ",")
	return f"🧾 {date} · {tx['payee']} · {amount} zł"


def question(tx, cats):
	if tx["category"]:
		text = f"{describe(tx)}\nKategoria: {tx['category']}"
		buttons = InlineKeyboardMarkup([[
			InlineKeyboardButton("✅ Dobrze", callback_data=f"rv:ok:{tx['id']}"),
			InlineKeyboardButton("✏️ Zmień", callback_data=f"rv:ch:{tx['id']}"),
		]])
		return text, buttons
	return f"{describe(tx)}\nKategoria: ❓ nie wiem, pomożesz?", category_picker(tx["id"], cats)


def category_picker(tx_id, cats):
	rows = [
		[InlineKeyboardButton(c["name"], callback_data=f"rv:set:{tx_id}:{i}") for i, c in list(enumerate(cats))[n:n + 2]]
		for n in range(0, len(cats), 2)
	]
	return InlineKeyboardMarkup(rows)


def remember_button(tx, index):
	return InlineKeyboardMarkup([[
		InlineKeyboardButton(f"🔁 Zawsze tak dla {tx['payee'][:30]}", callback_data=f"rv:rem:{tx['id']}:{index}"),
	]])
