import datetime
import json
import os
import subprocess

# Extra bank syncs on demand (when a receipt has no matching spending yet).
# Enable Banking / PSD2 allows about 4 unattended fetches per account a day and the
# 6:30 cron uses one, so keep a margin: at most 2 extra a day, 3 hours apart.
RUN = "/home/lbx/server/actual/bank-sync/run.sh"
LOG = "/home/lbx/server/actual/bank-sync/sync.log"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandora_bank_sync.json")
MAX_EXTRA_PER_DAY = 2
MIN_GAP = datetime.timedelta(hours=3)


def _load():
	try:
		with open(STATE_PATH) as f:
			return json.load(f)
	except FileNotFoundError:
		return {}


def _save(state):
	with open(STATE_PATH + ".tmp", "w") as f:
		json.dump(state, f)
	os.replace(STATE_PATH + ".tmp", STATE_PATH)


def last_sync():
	# The daily cron run appends to sync.log, and so do the extra runs
	try:
		return datetime.datetime.fromtimestamp(os.path.getmtime(LOG))
	except OSError:
		return None


def extra_today():
	state = _load()
	return state.get("count", 0) if state.get("date") == datetime.date.today().isoformat() else 0


def allowed():
	"""(True, None) or (False, reason in Polish)."""
	if extra_today() >= MAX_EXTRA_PER_DAY:
		return False, f"dziś pytałam już {MAX_EXTRA_PER_DAY} razy, bank więcej nie lubi"
	last = last_sync()
	if last and datetime.datetime.now() - last < MIN_GAP:
		return False, f"bank odświeżał się o {last.strftime('%H:%M')}, za wcześnie na kolejny raz"
	return True, None


def run():
	"""Pull new transactions into Actual now. Returns True on success."""
	today = datetime.date.today().isoformat()
	_save({"date": today, "count": extra_today() + 1})
	with open(LOG, "a") as log:
		log.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} extra sync for a receipt\n")
		log.flush()
		try:
			return subprocess.run([RUN], stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode == 0
		except subprocess.TimeoutExpired:
			return False


def describe():
	last = last_sync()
	text = f"last {last.strftime('%d.%m %H:%M')}" if last else "never"
	extra = extra_today()
	return text + (f" · {extra}/{MAX_EXTRA_PER_DAY} extra today" if extra else "")
