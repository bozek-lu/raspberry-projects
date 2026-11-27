"""Piano songs for the Tab5 tutor (Yamaha EZ-310 key lights).

Audio (YouTube link or an audio file sent to Pandora) goes into a queue first; then it is converted
here (fast or accurate), or later on a stronger computer with tools/piano-convert.py. MIDI files sent
to Pandora go straight to the songs. The work happens in the transcriber container (~/server/piano,
see transcribe.py); songs are served to the Tab5 on port 8095.
"""
import json
import os
import re
import subprocess
import time

COMPOSE_DIR = os.path.expanduser("~/server/piano")
SONGS_DIR = os.path.join(COMPOSE_DIR, "songs")
QUEUE_DIR = os.path.join(COMPOSE_DIR, "queue")
SONGS_URL = "http://homepi.local:8095"
# Accurate conversion of a long song can keep the Pi busy for hours
TIMEOUT = 6 * 60 * 60

# Create the data folders as this user: docker would create missing ones as root,
# and the transcriber (which runs as this user) couldn't write to them
for _dir in (SONGS_DIR, QUEUE_DIR):
	os.makedirs(_dir, exist_ok=True)

YOUTUBE_RE = re.compile(
	r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?\S*v=|shorts/|live/)|youtu\.be/)[\w-]{11}\S*")
MIDI_EXTENSIONS = (".mid", ".midi", ".kar")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".webm")


def find_link(text):
	match = YOUTUBE_RE.search(text or "")
	return match.group(0) if match else None


def kind_of(file_name, mime_type):
	"""'midi', 'audio' or None for a file sent to Pandora."""
	name, mime = (file_name or "").lower(), (mime_type or "").lower()
	if name.endswith(MIDI_EXTENSIONS) or "midi" in mime:
		return "midi"
	if name.endswith(AUDIO_EXTENSIONS) or mime.startswith("audio/"):
		return "audio"
	return None


def incoming_path(file_name, default_ext):
	"""Where Pandora saves a file received from Telegram (host path) for the transcriber to pick up."""
	ext = os.path.splitext(file_name or "")[1].lower() or default_ext
	return os.path.join(QUEUE_DIR, f"incoming-{int(time.time() * 1000)}{ext}")


def _container_path(host_path):
	return "/queue/" + os.path.basename(host_path)


def _run(*args):
	"""Blocking: one transcriber command. Returns its result dict (ok, ... or error)."""
	try:
		run = subprocess.run(["docker", "compose", "run", "--rm", "--build", "transcriber", *args],
			cwd=COMPOSE_DIR, capture_output=True, text=True, timeout=TIMEOUT)
	except subprocess.TimeoutExpired:
		return {"ok": False, "error": "trwało za długo"}
	results = [line for line in run.stdout.splitlines() if line.startswith("{")]
	if results:
		return json.loads(results[-1])
	errors = (run.stderr or "").strip().splitlines()
	return {"ok": False, "error": errors[-1] if errors else f"exit {run.returncode}"}


def fetch_link(url):
	return _run("fetch", url)


def search(words, count=8):
	"""YouTube search by song name -> {"ok", "results": [{id, title, url, seconds, channel, views, too_long}]}."""
	return _run("search", words, str(count))


def fetch_file(host_path, title):
	return _run("fetch", _container_path(host_path), title)


def convert(qid, fast):
	return _run("convert", qid, *(["fast"] if fast else []))


def add_midi(host_path, title):
	try:
		return _run("add-midi", _container_path(host_path), title)
	finally:
		if os.path.exists(host_path):
			os.remove(host_path)


def drop(qid):
	request_computer(qid, False)
	return _run("drop", qid)


def _index(path):
	try:
		with open(path) as f:
			return json.load(f)
	except (FileNotFoundError, json.JSONDecodeError):
		return []


def songs():
	return _index(os.path.join(SONGS_DIR, "songs.json"))


def queue():
	return _index(os.path.join(QUEUE_DIR, "queue.json"))


# --- converting on a computer (tools/piano-convert.py --watch on a Mac/PC) ----------------------
# Pandora's 💻 button and the tutor page put queue ids into computer.json; a computer running the
# watcher checks in every 30 s (.worker-<name> heartbeat files), claims an id (.claim-<id>), converts
# it with the accurate model and registers the result. Converting on the Pi stays the last resort.
COMPUTER_FILE = os.path.join(QUEUE_DIR, "computer.json")
ONLINE_SECONDS = 120


def _write(path, data):
	tmp = f"{path}.{os.getpid()}.tmp"
	with open(tmp, "w") as f:
		json.dump(data, f, ensure_ascii=False, indent=1)
	os.replace(tmp, path)


def computer_requests():
	"""{queue id: time requested} waiting for a computer."""
	try:
		with open(COMPUTER_FILE) as f:
			data = json.load(f)
		return data if isinstance(data, dict) else {}
	except (FileNotFoundError, json.JSONDecodeError):
		return {}


def request_computer(qid, wanted=True):
	requests = computer_requests()
	if wanted:
		requests[qid] = int(time.time())
		error = os.path.join(QUEUE_DIR, f"{qid}.computer-error")
		if os.path.exists(error):
			os.remove(error)
	else:
		requests.pop(qid, None)
	_write(COMPUTER_FILE, requests)


def workers():
	"""Computers running the watcher: [{name, device, busy, seen, online}] (newest first)."""
	out = []
	for name in os.listdir(QUEUE_DIR):
		if not name.startswith(".worker-"):
			continue
		try:
			with open(os.path.join(QUEUE_DIR, name)) as f:
				w = json.load(f)
		except (OSError, json.JSONDecodeError):
			continue
		w["online"] = time.time() - w.get("seen", 0) < ONLINE_SECONDS
		out.append(w)
	return sorted(out, key=lambda w: -w.get("seen", 0))


def computer_state(qid):
	"""(state, detail) of a queue item on the computer side: claimed / requested / failed / None."""
	claim = os.path.join(QUEUE_DIR, f".claim-{qid}")
	if os.path.exists(claim):
		with open(claim) as f:
			who = f.read().strip()
		if any(w["online"] and w.get("name") == who for w in workers()):
			return "computer", who
	error = os.path.join(QUEUE_DIR, f"{qid}.computer-error")
	if os.path.exists(error):
		with open(error) as f:
			return "computer-failed", f.read().strip()[-300:]
	if qid in computer_requests():
		return "for-computer", None
	return None, None


def converting_on_pi():
	"""Queue ids the transcriber container is converting right now (from Pandora, the tutor or a leftover)."""
	try:
		run = subprocess.run(["docker", "ps", "--no-trunc", "--filter", "label=com.docker.compose.service=transcriber",
			"--format", "{{.Command}}"], capture_output=True, text=True, timeout=10)
	except (OSError, subprocess.TimeoutExpired):
		return set()
	return set(re.findall(r"transcribe\.py convert ([\w-]+)", run.stdout))


def song_path(song):
	return os.path.join(SONGS_DIR, song["file"])


def length(seconds):
	minutes, seconds = divmod(int(seconds or 0), 60)
	return f"{minutes}:{seconds:02d}"


def estimate(seconds, fast):
	"""Rough Pi 4 conversion time in minutes (measured: basic-pitch ~0.8x, ByteDance ~20x song length)."""
	return max(1, round((seconds or 0) * (0.8 if fast else 20) / 60 + 0.5))


def describe(song):
	notes = f", {song['notes']} nut" if song.get("notes") is not None else ""
	return f"{song.get('title', song.get('file', song.get('id')))} ({length(song.get('seconds'))}{notes})"
