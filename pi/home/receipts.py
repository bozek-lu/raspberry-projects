import datetime
import json
import os
import re
import time

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from claude_client import ask_claude

# Receipts and invoices sent to Pandora: stored in Paperless-ngx, then linked to the
# matching Actual transaction through the actual-bridge container.
PAPERLESS = "http://127.0.0.1:8010"
PUBLIC_URL = "https://homepi.tail04e168.ts.net:8444"
BRIDGE = "http://127.0.0.1:5007"
TAG = "paragon"
HERE = os.path.dirname(os.path.abspath(__file__))
# Receipts still waiting for their bank transaction
STATE_PATH = os.path.join(HERE, "pandora_receipts.json")
# Every receipt and what became of it, for the Homepage tile
LOG_PATH = os.path.join(HERE, "pandora_receipts_log.json")
TILE_PATH = "/home/lbx/server/homepage/public/receipts.json"
LOG_KEEP = 400
# Bank card payments can post days later; keep looking for this long
RETRY_DAYS = 10
OCR_TIMEOUT = 15 * 60
MARK = "📎 Paperless #"

EXTRACT_PROMPT = """Below is OCR text of a scanned document (usually a Polish shop receipt or invoice).
Answer with JSON only, no other text:
{{"kind": "receipt" | "invoice" | "other", "merchant": short shop or company name or null,
"date": purchase date as "YYYY-MM-DD" or null, "total": total amount paid as a number or null,
"currency": "PLN" or another ISO code}}

OCR text:
{text}"""


def _paperless():
	return httpx.Client(base_url=PAPERLESS, timeout=60,
		headers={"Authorization": f"Token {os.getenv('PAPERLESS_TOKEN')}"})


def link(doc_id):
	return f"{PUBLIC_URL}/documents/{doc_id}/details"


# --- Paperless ---

def _tag_id(client):
	found = client.get("/api/tags/", params={"name__iexact": TAG}).raise_for_status().json()["results"]
	if found:
		return found[0]["id"]
	return client.post("/api/tags/", json={"name": TAG, "color": "#e67e22"}).raise_for_status().json()["id"]


def upload(path, filename, title=None):
	"""Send a file to Paperless; returns the consume task id."""
	with _paperless() as client:
		data = {"tags": _tag_id(client)}
		if title:
			data["title"] = title
		with open(path, "rb") as f:
			r = client.post("/api/documents/post_document/", files={"document": (filename, f)}, data=data)
		return r.raise_for_status().json()


def wait_for_document(task_id):
	"""Wait for OCR to finish. Returns (document id, None) or (None, error text)."""
	# Works with both task APIs: v2 (plain list, "SUCCESS", related_document)
	# and v3 (paginated, "success", related_document_ids, result_data)
	deadline = time.time() + OCR_TIMEOUT
	with _paperless() as client:
		while time.time() < deadline:
			data = client.get("/api/tasks/", params={"task_id": task_id}).raise_for_status().json()
			tasks = data.get("results", []) if isinstance(data, dict) else data
			task = tasks[0] if tasks else {}
			status = (task.get("status") or "").upper()
			doc_ids = task.get("related_document_ids") or ([task["related_document"]] if task.get("related_document") else [])
			if status == "SUCCESS" and doc_ids:
				return int(doc_ids[0]), None
			if status in ("FAILURE", "REVOKED"):
				error = task.get("result") or task.get("result_data") or "nieznany błąd"
				if isinstance(error, dict):
					error = error.get("error") or error.get("message") or json.dumps(error, ensure_ascii=False)
				return None, str(error)[:300]
			time.sleep(5)
	return None, "Paperless nie skończył w 15 minut"


def document(doc_id):
	with _paperless() as client:
		return client.get(f"/api/documents/{doc_id}/").raise_for_status().json()


def duplicates(doc):
	"""Earlier copies of exactly the same file (Paperless v3 keeps both and flags them)."""
	return [d for d in doc.get("duplicate_documents") or [] if not d.get("deleted_at") and d["id"] < doc["id"]]


def delete(doc_id):
	# Goes to the Paperless trash, which keeps it for 30 days
	with _paperless() as client:
		client.delete(f"/api/documents/{doc_id}/").raise_for_status()
	forget(doc_id)
	set_status(doc_id, "deleted")


def rename(doc_id, info):
	parts = [info.get("date"), info.get("merchant"), _zl(info["total"]) if info.get("total") else None]
	fields = {"title": " · ".join(p for p in parts if p)}
	if info.get("date"):
		fields["created"] = info["date"]
	with _paperless() as client:
		client.patch(f"/api/documents/{doc_id}/", json=fields).raise_for_status()


# --- reading the receipt ---

def extract(text):
	"""Shop, date and total from the OCR text, via Claude (no tools, text only)."""
	if not text or not text.strip():
		return None
	answer = ask_claude(EXTRACT_PROMPT.format(text=text[:6000]), "You extract data from documents. Reply with JSON only.")
	match = re.search(r"\{.*\}", answer or "", re.S)
	if not match:
		return None
	try:
		info = json.loads(match.group(0))
		if info.get("date"):
			datetime.date.fromisoformat(info["date"])
		if info.get("total") is not None:
			info["total"] = round(float(info["total"]), 2)
	except (ValueError, TypeError):
		return None
	return info


def matchable(info):
	return bool(info and info.get("kind") != "other" and info.get("total") and info.get("date")
		and (info.get("currency") or "PLN") == "PLN")


# --- Actual ---

def candidates(info):
	"""Matching spending, split into (without a receipt, already with a receipt)."""
	amount = round(info["total"] * 100)
	url = f"{BRIDGE}/match?amount={amount}&date={info['date']}"
	found = httpx.get(url, timeout=60).raise_for_status().json()["transactions"]
	free = [t for t in found if MARK not in t["notes"]]
	taken = [t for t in found if MARK in t["notes"]]
	return free, taken


def receipt_of(tx):
	"""Paperless id of the receipt already attached to this transaction."""
	found = re.search(re.escape(MARK) + r"(\d+)", tx["notes"])
	return int(found.group(1)) if found else None


def attach(doc_id, tx):
	note = f"{MARK}{doc_id} {link(doc_id)}"
	notes = f"{tx['notes']}\n{note}".strip() if tx["notes"] else note
	httpx.post(f"{BRIDGE}/notes", json={"id": tx["id"], "notes": notes}, timeout=60).raise_for_status()
	forget(doc_id)
	set_status(doc_id, "linked")


# --- receipts still waiting for their bank transaction ---

def _load():
	try:
		with open(STATE_PATH) as f:
			return json.load(f)
	except FileNotFoundError:
		return {}


def _save(state):
	with open(STATE_PATH + ".tmp", "w") as f:
		json.dump(state, f, ensure_ascii=False, indent=1)
	os.replace(STATE_PATH + ".tmp", STATE_PATH)


def remember(doc_id, info):
	state = _load()
	state[str(doc_id)] = {**info, "added": datetime.date.today().isoformat()}
	_save(state)
	set_status(doc_id, "waiting")


def forget(doc_id):
	state = _load()
	if state.pop(str(doc_id), None) is not None:
		_save(state)


# --- receipt log and the Homepage tile ---

def _load_log():
	try:
		with open(LOG_PATH) as f:
			return json.load(f)
	except FileNotFoundError:
		return {}


def log_receipt(doc_id, info, status):
	log = _load_log()
	log[str(doc_id)] = {"added": datetime.datetime.now().isoformat(timespec="minutes"), "status": status,
		**{k: (info or {}).get(k) for k in ("merchant", "date", "total")}}
	_save_log(log)


def set_status(doc_id, status):
	log = _load_log()
	if str(doc_id) in log:
		log[str(doc_id)]["status"] = status
		_save_log(log)


def _save_log(log):
	if len(log) > LOG_KEEP:
		log = dict(sorted(log.items(), key=lambda kv: kv[1]["added"])[-LOG_KEEP:])
	with open(LOG_PATH + ".tmp", "w") as f:
		json.dump(log, f, ensure_ascii=False, indent=1)
	os.replace(LOG_PATH + ".tmp", LOG_PATH)
	try:
		write_tile()
	except Exception as e:
		print(f"Receipts tile failed: {e}")


STATUS_LABELS = {"linked": "in budget", "waiting": "waiting for bank", "duplicate": "duplicate",
	"unread": "no amount found", "unmatched": "not in budget", "deleted": "deleted"}


def write_tile():
	import banksync
	log = {k: v for k, v in _load_log().items() if v["status"] != "deleted"}
	month = datetime.date.today().strftime("%Y-%m")
	this_month = [v for v in log.values() if v["added"].startswith(month)]
	linked = sum(v["status"] == "linked" for v in this_month)
	waiting = [v for v in log.values() if v["status"] == "waiting"]
	last = max(log.values(), key=lambda v: v["added"], default=None)
	try:
		with _paperless() as client:
			documents = client.get("/api/documents/", params={"page_size": 1}, timeout=3).json()["count"]
	except Exception:
		documents = "?"
	tile = {
		"month": f"{len(this_month)} · {linked} in budget" if this_month else "none yet",
		"waiting": ", ".join(_short(v) for v in waiting[:3]) + (f" +{len(waiting) - 3}" if len(waiting) > 3 else "") if waiting else "none",
		"last": f"{_short(last)} · {STATUS_LABELS.get(last['status'], last['status'])}" if last else "–",
		"documents": documents,
		"bank_sync": banksync.describe(),
		"updated": datetime.datetime.now().strftime("%d.%m %H:%M"),
	}
	with open(TILE_PATH + ".tmp", "w") as f:
		json.dump(tile, f, ensure_ascii=False)
	os.replace(TILE_PATH + ".tmp", TILE_PATH)


def _short(entry):
	parts = [entry.get("merchant") or "?"]
	if entry.get("total"):
		parts.append(_zl(entry["total"]))
	return " ".join(parts)


def pending():
	return {int(k): v for k, v in _load().items()}


def expired(info):
	added = datetime.date.fromisoformat(info["added"])
	return (datetime.date.today() - added).days > RETRY_DAYS


def pending_info(doc_id):
	return _load().get(str(doc_id))


# --- texts and buttons ---

def _zl(amount):
	return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " zł"


def describe(info):
	parts = [info.get("merchant") or "?", _zl(info["total"])]
	if info.get("date"):
		parts.insert(0, datetime.date.fromisoformat(info["date"]).strftime("%d.%m"))
	return " · ".join(parts)


def describe_tx(tx):
	date = datetime.date.fromisoformat(tx["date"]).strftime("%d.%m")
	return f"{date} · {tx['payee']} · {tx['account']}"


def picker(doc_id, txs):
	rows = [[InlineKeyboardButton(describe_tx(t)[:60], callback_data=f"rc:{doc_id}:{t['id']}")] for t in txs[:6]]
	rows.append([InlineKeyboardButton("✖️ Żaden z nich", callback_data=f"rc:{doc_id}:none")])
	return InlineKeyboardMarkup(rows)


def duplicate_buttons(doc_id):
	return InlineKeyboardMarkup([[
		InlineKeyboardButton("🗑️ Usuń nowy", callback_data=f"rd:{doc_id}:del"),
		InlineKeyboardButton("📚 Zostaw oba", callback_data=f"rd:{doc_id}:keep"),
	]])
