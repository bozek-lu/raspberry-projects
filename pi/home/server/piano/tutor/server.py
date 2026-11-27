#!/usr/bin/env python3
"""Piano tutor server on homepi (systemd unit piano-tutor.service, port 8096, standard library only).

Serves the tablet page (index.html) and the songs, and adds what a static server can't:
  GET  /api/stats             practice summary for the Homepage tile
  GET  /api/progress          per-song practice: plays, best accuracy, minutes, last time
  POST /api/session           the page reports a finished practice session
  GET  /api/queue             songs waiting for conversion, plus jobs running from here
  GET  /api/search?q=&piano=1 YouTube search by song name ("piano" added unless piano=0), marks songs already here
  POST /api/add               {"url", "how"}  YouTube link -> queue, then convert as "how" says
  POST /api/upload?name=&title=&how=   raw file body: MIDI -> songs, audio -> queue
  POST /api/convert           {"id", "how"}  how: "computer" (accurate, on a Mac/PC running
                              tools/piano-convert.py --watch), "pi-fast" or "pi-accurate" (last resort)
  POST /api/drop              {"id"}
  POST /api/song/rename       {"file", "title"}
  POST /api/song/delete       {"file"}
Adding and converting reuse Pandora's ~/piano.py (the transcriber container does the work).
Practice history lives in ~/server/piano/practice.json.
"""
import datetime
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOME = Path.home()
sys.path.insert(0, str(HOME))
import piano  # noqa: E402  (Pandora's helpers: queue, convert, add_midi, ...)

HERE = Path(__file__).resolve().parent
SONGS = Path(piano.SONGS_DIR)
PRACTICE = Path(piano.COMPOSE_DIR) / "practice.json"
PORT = int(os.environ.get("PORT", "8096"))
MAX_UPLOAD = 60 * 1024 * 1024
DAY_START_HOUR = 4                  # practice after midnight still counts for the evening before

lock = threading.Lock()             # practice.json and songs.json writes
jobs = {}                           # id or upload key -> {"state", "title", "fast", "started", "error"}
convert_lock = threading.Lock()     # one conversion at a time from here (each can take an hour)


def log(*args):
	print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, flush=True)


def read_json(path, fallback):
	try:
		return json.loads(Path(path).read_text())
	except (OSError, ValueError):
		return fallback


def write_json(path, data):
	path = Path(path)
	fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
	with os.fdopen(fd, "w") as f:
		json.dump(data, f, ensure_ascii=False, indent=1)
	os.replace(tmp, path)


# --- practice statistics ------------------------------------------------------------------------

def practice_day(ts):
	return (datetime.datetime.fromtimestamp(ts) - datetime.timedelta(hours=DAY_START_HOUR)).date()


def duration(minutes):
	minutes = int(round(minutes))
	return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def accuracy(s):
	total = s.get("good", 0) + s.get("bad", 0)
	return round(100 * s.get("good", 0) / total) if total else None


def short(title, size=30):
	title = title or "?"
	return title if len(title) <= size else title[:size - 1] + "…"


def days_text(n):
	return f"{n} day" + ("" if n == 1 else "s")


def when(ts):
	"""'today 19:40', 'yesterday 08:15', '3 days ago'."""
	ago = (practice_day(time.time()) - practice_day(ts)).days
	clock = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
	return f"today {clock}" if ago == 0 else f"yesterday {clock}" if ago == 1 else f"{ago} days ago"


def stats():
	"""Practice summary: the tutor page's drawer and the Homepage tile (text fields ready to show)."""
	sessions = read_json(PRACTICE, [])
	songs = piano.songs()
	titles = {s["file"]: s.get("title") for s in songs}
	today = practice_day(time.time())
	week_start = today - datetime.timedelta(days=today.weekday())
	month_start = today.replace(day=1)
	minutes_by_day, minutes_by_song_month = {}, {}
	for s in sessions:
		day = practice_day(s["ts"])
		minutes_by_day[day] = minutes_by_day.get(day, 0) + s.get("seconds", 0) / 60
		if day >= month_start:
			minutes_by_song_month[s["file"]] = minutes_by_song_month.get(s["file"], 0) + s.get("seconds", 0) / 60
	streak, day = 0, today
	if minutes_by_day.get(day, 0) < 1:
		day -= datetime.timedelta(days=1)       # today doesn't break the streak until it's over
	while minutes_by_day.get(day, 0) >= 1:
		streak += 1
		day -= datetime.timedelta(days=1)
	best_streak = run = 0
	previous = None
	for d in sorted(d for d, m in minutes_by_day.items() if m >= 1):
		run = run + 1 if previous and (d - previous).days == 1 else 1
		best_streak = max(best_streak, run)
		previous = d
	week = sum(m for d, m in minutes_by_day.items() if d >= week_start)
	month = sum(m for d, m in minutes_by_day.items() if d >= month_start)
	week_sessions = sum(1 for s in sessions if practice_day(s["ts"]) >= week_start)

	last_text = last_when = "-"
	if sessions:
		last = sessions[-1]
		acc = accuracy(last)
		last_text = short(titles.get(last["file"]) or last.get("title") or last["file"])
		if acc is not None:
			last_text += f" · {acc}%"
		last_when = when(last["ts"])
	top = max(minutes_by_song_month.items(), key=lambda kv: kv[1], default=None)
	top_text = f"{short(titles.get(top[0]) or top[0])} · {duration(top[1])}" if top else "-"
	best = None
	for file, p in progress().items():
		if p["best"] is not None and file in titles and (best is None or (p["best"], p["best_tempo"]) > best[1:]):
			best = (file, p["best"], p["best_tempo"])
	best_text = "-"
	if best:
		best_text = f"{short(titles[best[0]])} · {best[1]}%"
		if best[2] and best[2] != 1:
			best_text += f" at {round(best[2] * 100)}%"

	waiting = len(piano.queue())
	converting = sum(1 for j in jobs.values() if j["state"] in ("converting", "fetching"))
	queue_text = ", ".join(t for t in (f"{converting} in progress" if converting else "",
		f"{waiting - min(waiting, converting)} waiting" if waiting > converting else "") if t) or "nothing"
	practised = len({s["file"] for s in sessions if s["file"] in titles})
	return {
		"today": duration(minutes_by_day.get(today, 0)),
		"today_minutes": round(minutes_by_day.get(today, 0)),
		"week": duration(week),
		"week_text": duration(week) + (f" · {week_sessions} session" + ("" if week_sessions == 1 else "s") if week_sessions else ""),
		"week_minutes": round(week),
		"month": duration(month),
		"total": duration(sum(minutes_by_day.values())),
		"streak": days_text(streak),
		"streak_days": streak,
		"best_streak": days_text(best_streak),
		"last": last_text,
		"last_when": last_when,
		"top_song": top_text,
		"best_song": best_text,
		"songs": f"{len(songs)} ({practised} practised)",
		"song_count": len(songs),
		"queue": queue_text,
		"sessions": len(sessions),
		"days": {str(d): round(m, 1) for d, m in sorted(minutes_by_day.items()) if d >= today - datetime.timedelta(days=27)},
	}


def progress():
	out = {}
	for s in read_json(PRACTICE, []):
		p = out.setdefault(s["file"], {"plays": 0, "completed": 0, "best": None, "best_tempo": None, "minutes": 0, "last": None})
		p["plays"] += 1
		p["minutes"] = round(p["minutes"] + s.get("seconds", 0) / 60, 1)
		p["last"] = s["ts"]
		acc = accuracy(s)
		if s.get("completed") and acc is not None:
			p["completed"] += 1
			score = (acc, s.get("tempo", 1))
			if p["best"] is None or score > (p["best"], p["best_tempo"]):
				p["best"], p["best_tempo"] = score
	return out


def add_session(body):
	file = str(body.get("file", ""))
	seconds = float(body.get("seconds", 0))
	if not file or not 5 <= seconds <= 6 * 3600:
		raise ValueError("bad session")
	session = {
		"ts": round(time.time()), "file": file, "title": str(body.get("title", ""))[:200],
		"mode": body.get("mode") if body.get("mode") in ("wait", "play") else "wait",
		"hands": body.get("hands") if body.get("hands") in ("L", "R", "B") else "B",
		"tempo": round(float(body.get("tempo", 1)), 2), "seconds": round(seconds),
		"good": int(body.get("good", 0)), "bad": int(body.get("bad", 0)),
		"completed": bool(body.get("completed")), "loop": bool(body.get("loop")),
	}
	with lock:
		sessions = read_json(PRACTICE, [])
		sessions.append(session)
		write_json(PRACTICE, sessions)
	log("session", session["file"], session["seconds"], "s", accuracy(session), "%")
	return session


# --- songs and the conversion queue -------------------------------------------------------------

def song_file(name):
	name = str(name or "")
	if Path(name).name != name or not name.lower().endswith(piano.MIDI_EXTENSIONS):
		raise ValueError("bad song name")
	return name


def rename_song(file, title):
	file, title = song_file(file), " ".join(str(title or "").split())[:200]
	if not title:
		raise ValueError("empty title")
	with lock:
		index = piano.songs()
		for s in index:
			if s["file"] == file:
				s["title"] = title
				break
		else:
			raise ValueError("unknown song")
		write_json(SONGS / "songs.json", index)


def delete_song(file):
	file = song_file(file)
	with lock:
		write_json(SONGS / "songs.json", [s for s in piano.songs() if s["file"] != file])
		try:
			(SONGS / file).unlink()
		except FileNotFoundError:
			pass
	log("deleted song", file)


def convert_as(qid, how):
	"""computer: hand it to a Mac/PC (the default for accurate notes); pi-fast / pi-accurate: here."""
	if how == "computer":
		job = jobs.get(qid, {})
		if job.get("state") == "converting":
			raise ValueError("already converting on the Pi")
		if job.get("state") == "waiting":
			job["cancel"] = True                # its thread drops it when its turn comes
		else:
			jobs.pop(qid, None)                 # e.g. an earlier failure on the Pi
		piano.request_computer(qid)
		log("for computer", qid)
	elif how in ("pi-fast", "pi-accurate"):
		start_convert(qid, how == "pi-fast")
	elif how:
		raise ValueError(f"unknown conversion: {how}")


def run_job(key, title, work, then_how=None):
	"""Background job (fetch, upload, convert), visible in /api/queue until it ends."""
	def target():
		try:
			result = work()
			if not result.get("ok"):
				jobs[key].update(state="failed", error=result.get("error") or "failed")
				log("job failed", key, result.get("error"))
				return
			log("job done", key, result.get("title") or result.get("file") or result.get("id"))
			jobs.pop(key, None)
			if then_how and result.get("id") and not result.get("file"):
				convert_as(result["id"], then_how)
		except Exception as e:
			traceback.print_exc()
			jobs[key].update(state="failed", error=str(e))
	jobs[key] = {"state": "fetching", "title": title, "started": time.time()}
	threading.Thread(target=target, daemon=True).start()


def start_convert(qid, fast):
	entry = next((e for e in piano.queue() if e["id"] == qid), None)
	if not entry:
		raise ValueError("not in the queue")
	if jobs.get(qid, {}).get("state") in ("waiting", "converting") or qid in piano.converting_on_pi():
		return
	piano.request_computer(qid, False)
	jobs[qid] = {"state": "waiting", "title": entry.get("title"), "fast": fast, "started": time.time(),
		"minutes": piano.estimate(entry.get("seconds"), fast)}

	def target():
		with convert_lock:
			if jobs.get(qid, {}).get("cancel"):     # moved to a computer while waiting
				jobs.pop(qid, None)
				return
			jobs[qid].update(state="converting", started=time.time())
			log("converting", qid, "fast" if fast else "accurate")
			try:
				result = piano.convert(qid, fast)
			except Exception as e:
				traceback.print_exc()
				result = {"ok": False, "error": str(e)}
			if result.get("ok"):
				jobs.pop(qid, None)
				log("converted", qid, result.get("file"))
			else:
				jobs[qid].update(state="failed", error=result.get("error") or "failed")
	threading.Thread(target=target, daemon=True).start()


def queue_view():
	"""{"items": queue with where/how each item is being converted, "workers": computers running the watcher}."""
	items = []
	on_pi = piano.converting_on_pi()
	for e in piano.queue():
		job = jobs.get(e["id"], {})
		if job.get("cancel"):
			job = {}
		state, detail = job.get("state"), job.get("error")
		if not state or state == "failed":
			cstate, cdetail = piano.computer_state(e["id"])
			if cstate:
				state, detail = cstate, cdetail
		if e["id"] in on_pi and state != "converting":
			state = "converting"                # started by Pandora, or before a restart of this server
		items.append({**e, "state": state or "queued", "error": detail if state in ("failed", "computer-failed") else None,
			"worker": detail if state == "computer" else None, "fast": job.get("fast"),
			"minutes": job.get("minutes"), "started": job.get("started"),
			"estimate_fast": piano.estimate(e.get("seconds"), True), "estimate_accurate": piano.estimate(e.get("seconds"), False)})
	known = {i["id"] for i in items}
	for key, job in list(jobs.items()):
		if key not in known and job["state"] in ("fetching", "failed"):
			items.append({"id": key, "title": job.get("title"), "state": job["state"], "error": job.get("error"),
				"started": job.get("started")})
	return {"items": items, "workers": [{k: w.get(k) for k in ("name", "device", "busy", "online", "seen")} for w in piano.workers()]}


search_cache = {}                   # query -> (time, response)


def search(query):
	words = " ".join(query.get("q", [""])[0].split())[:120]
	if len(words) < 2:
		raise ValueError("type a song name")
	if query.get("piano", ["1"])[0] != "0" and "piano" not in words.lower():
		words += " piano"
	cached = search_cache.get(words)
	if cached and time.time() - cached[0] < 600:
		result = cached[1]
	else:
		result = piano.search(words, 10)
		if not result.get("ok"):
			raise ValueError(result.get("error") or "search failed")
		search_cache[words] = (time.time(), result)
		log("search", repr(words), len(result.get("results", [])), "results")
	songs = {re.sub(r".*[?&]v=|.*youtu\.be/", "", s.get("url") or "")[:11]: s["file"] for s in piano.songs() if s.get("url")}
	queued = {e["id"] for e in piano.queue()}
	busy = {job.get("title") for job in jobs.values() if job["state"] == "fetching"}
	results = [{**r, "song": songs.get(r["id"]), "queued": r["id"] in queued or r["url"] in busy}
		for r in result.get("results", [])]
	return {"ok": True, "query": words, "max_seconds": result.get("max_seconds"), "results": results}


def add_url(body):
	url = piano.find_link(str(body.get("url", "")))
	if not url:
		raise ValueError("not a YouTube link")
	how = body.get("how")
	key = f"url-{int(time.time() * 1000)}"
	run_job(key, url, lambda: piano.fetch_link(url), then_how=how if how in ("computer", "pi-fast", "pi-accurate") else None)
	return {"ok": True, "job": key}


def upload(query, read_body, length, content_type):
	name = Path(query.get("name", ["upload"])[0]).name
	title = " ".join(query.get("title", [""])[0].split()) or Path(name).stem
	kind = piano.kind_of(name, content_type)
	if not kind:
		raise ValueError("send a MIDI or audio file")
	if not 0 < length <= MAX_UPLOAD:
		raise ValueError(f"file too big (max {MAX_UPLOAD // 1024 // 1024} MB)")
	path = piano.incoming_path(name, ".mid" if kind == "midi" else ".mp3")
	with open(path, "wb") as f:
		remaining = length
		while remaining:
			chunk = read_body(min(remaining, 1 << 20))
			if not chunk:
				break
			f.write(chunk)
			remaining -= len(chunk)
	if kind == "midi":
		result = piano.add_midi(path, title)   # quick; removes the upload
		if not result.get("ok"):
			raise ValueError(result.get("error") or "could not add the MIDI file")
		return result
	how = query.get("how", [""])[0]
	key = f"file-{int(time.time() * 1000)}"
	run_job(key, title, lambda: piano.fetch_file(path, title),
		then_how=how if how in ("computer", "pi-fast", "pi-accurate") else None)
	return {"ok": True, "job": key}


# --- HTTP ---------------------------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
	server_version = "piano-tutor"

	def __init__(self, *args, **kwargs):
		super().__init__(*args, directory=str(SONGS), **kwargs)

	def log_message(self, fmt, *args):
		pass

	def end_headers(self):
		self.send_header("Cache-Control", "no-cache")
		super().end_headers()

	def send_json(self, data, status=HTTPStatus.OK):
		body = json.dumps(data, ensure_ascii=False).encode()
		self.send_response(status)
		self.send_header("Content-Type", "application/json; charset=utf-8")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		url = urlparse(self.path)
		routes = {"/api/stats": stats, "/api/progress": progress, "/api/queue": queue_view}
		if url.path in routes:
			return self.send_json(routes[url.path]())
		if url.path == "/api/search":
			try:
				return self.send_json(search(parse_qs(url.query)))
			except ValueError as e:
				return self.send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
		if url.path in ("/", "/index.html"):
			self.path = "/__page__"
			return self.send_page()
		if url.path.startswith("/songs/"):
			self.path = url.path[len("/songs"):]
			return super().do_GET()
		self.send_error(HTTPStatus.NOT_FOUND)

	def send_page(self):
		body = (HERE / "index.html").read_bytes()
		self.send_response(HTTPStatus.OK)
		self.send_header("Content-Type", "text/html; charset=utf-8")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_POST(self):
		url = urlparse(self.path)
		length = int(self.headers.get("Content-Length") or 0)
		try:
			if url.path == "/api/upload":
				return self.send_json(upload(parse_qs(url.query), self.rfile.read, length,
					self.headers.get("Content-Type", "")))
			if length > 64 * 1024:
				raise ValueError("request too big")
			body = json.loads(self.rfile.read(length) or b"{}")
			if url.path == "/api/session":
				return self.send_json(add_session(body))
			if url.path == "/api/add":
				return self.send_json(add_url(body))
			if url.path == "/api/convert":
				qid = str(body.get("id", ""))
				if not any(e["id"] == qid for e in piano.queue()):
					raise ValueError("not in the queue")
				convert_as(qid, str(body.get("how") or ""))
				return self.send_json({"ok": True})
			if url.path == "/api/drop":
				qid = str(body.get("id", ""))
				job = jobs.get(qid, {})
				if job.get("state") == "converting":
					raise ValueError("it is being converted right now")
				if job.get("state") == "failed" and not any(e["id"] == qid for e in piano.queue()):
					jobs.pop(qid, None)
					return self.send_json({"ok": True})
				jobs.pop(qid, None)
				result = piano.drop(qid)
				return self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
			if url.path == "/api/song/rename":
				rename_song(body.get("file"), body.get("title"))
				return self.send_json({"ok": True})
			if url.path == "/api/song/delete":
				delete_song(body.get("file"))
				return self.send_json({"ok": True})
			self.send_error(HTTPStatus.NOT_FOUND)
		except (ValueError, KeyError, TypeError) as e:
			log("refused", url.path, e)
			self.send_json({"ok": False, "error": str(e)}, HTTPStatus.BAD_REQUEST)
		except Exception as e:
			traceback.print_exc()
			self.send_json({"ok": False, "error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main():
	server = ThreadingHTTPServer(("", PORT), Handler)
	server.daemon_threads = True
	log(f"piano tutor on :{PORT}, songs in {SONGS}")
	server.serve_forever()


if __name__ == "__main__":
	main()
