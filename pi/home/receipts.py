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
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandora_receipts.json")
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
	amount = round(info["total"] * 100)
	url = f"{BRIDGE}/match?amount={amount}&date={info['date']}"
	found = httpx.get(url, timeout=60).raise_for_status().json()["transactions"]
	# A transaction that already has a receipt is not a candidate again
	return [t for t in found if MARK not in t["notes"]]


def attach(doc_id, tx):
	note = f"{MARK}{doc_id} {link(doc_id)}"
	notes = f"{tx['notes']}\n{note}".strip() if tx["notes"] else note
	httpx.post(f"{BRIDGE}/notes", json={"id": tx["id"], "notes": notes}, timeout=60).raise_for_status()


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


def forget(doc_id):
	state = _load()
	if state.pop(str(doc_id), None) is not None:
		_save(state)


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
