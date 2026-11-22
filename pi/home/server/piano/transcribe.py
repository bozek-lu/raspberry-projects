"""Piano songs for the EZ-310 tutor: audio (YouTube link or file) -> queue -> piano MIDI.

  transcribe.py fetch <url | /queue/file> [title]  download / import audio into the queue (16 kHz mono WAV)
  transcribe.py convert <id> [fast]                transcribe a queued item here: ByteDance (accurate, ~20x song
                                                   length on a Pi 4) or basic-pitch with "fast" (~1x, rough)
  transcribe.py register <id>                      finish an item transcribed elsewhere (/queue/<id>.raw.mid,
                                                   see tools/piano-convert.py)
  transcribe.py add-midi <path> <title>            add a ready MIDI file to the songs
  transcribe.py drop <id>                          remove an item from the queue
  transcribe.py <url>                              fetch + convert in one go

Songs go to SONGS_DIR (index songs.json), the queue to QUEUE_DIR (index queue.json).
Every command prints one JSON line: {"ok": true, ...} or {"ok": false, "error": ...}.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave

import mido

SONGS_DIR = os.environ.get("SONGS_DIR", "/songs")
QUEUE_DIR = os.environ.get("QUEUE_DIR", "/queue")
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "900"))
# What the transcription model expects
SAMPLE_RATE = 16000
# Middle C and up -> channel 1 (right hand), below -> channel 2 (left hand).
# These are the EZ-310 default light channels (Function 042 / 043).
SPLIT_NOTE = 60
RIGHT, LEFT = 0, 1


def finish(**result):
	print(json.dumps(result, ensure_ascii=False))
	sys.exit(0 if result.get("ok") else 1)


def slug(text):
	# ASCII file names: "Krótki łabędź" -> "krotki-labedz"
	import unicodedata
	text = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L")).encode("ascii", "ignore").decode()
	return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "song"


def now():
	return datetime.datetime.now().isoformat(timespec="seconds")


def load_index(path):
	try:
		with open(path) as f:
			return json.load(f)
	except (FileNotFoundError, json.JSONDecodeError):
		return []


def save_index(path, items):
	tmp = path + ".tmp"
	with open(tmp, "w") as f:
		json.dump(items, f, ensure_ascii=False, indent=1)
	os.replace(tmp, path)


def upsert(path, entry, key):
	save_index(path, [e for e in load_index(path) if e.get(key) != entry[key]] + [entry])


QUEUE_INDEX = os.path.join(QUEUE_DIR, "queue.json")
SONGS_INDEX = os.path.join(SONGS_DIR, "songs.json")


def queued(qid):
	entry = next((e for e in load_index(QUEUE_INDEX) if e["id"] == qid), None)
	if not entry:
		finish(ok=False, error=f"not in the queue: {qid}")
	return entry


def queue_audio(src, key, title, url):
	"""Any audio file -> 16 kHz mono WAV in the queue (small, and any machine can read it without ffmpeg)."""
	qid = re.sub(r"[^A-Za-z0-9_-]", "", key or "")[:20] or f"f{int(time.time())}"
	dest = os.path.join(QUEUE_DIR, f"{qid}.wav")
	run = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", str(SAMPLE_RATE), dest],
		capture_output=True, text=True)
	if run.returncode != 0:
		finish(ok=False, error="ffmpeg: " + ((run.stderr or "").strip().splitlines() or ["cannot read audio"])[-1])
	with wave.open(dest) as w:
		seconds = round(w.getnframes() / w.getframerate())
	if seconds > MAX_SECONDS:
		os.remove(dest)
		finish(ok=False, error=f"longer than {MAX_SECONDS // 60} min")
	entry = {"id": qid, "title": title or qid, "url": url, "seconds": seconds, "added": now()}
	upsert(QUEUE_INDEX, entry, "id")
	return entry


def fetch_url(url):
	"""YouTube (or anything yt-dlp knows) -> queue."""
	with tempfile.TemporaryDirectory() as work:
		cmd = ["yt-dlp", "--no-playlist", "--quiet", "--no-warnings",
			"--match-filter", f"duration <= {MAX_SECONDS} & !is_live",
			"-f", "bestaudio/best", "-o", os.path.join(work, "audio.%(ext)s"),
			"-j", "--no-simulate", url]
		# YouTube often answers 403 to a download that works a moment later
		for attempt in range(5):
			run = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
			if run.returncode == 0:
				break
			time.sleep(5 * (attempt + 1))
		if run.returncode != 0:
			lines = (run.stderr or "").strip().splitlines()
			finish(ok=False, error="yt-dlp: " + (lines[-1] if lines else f"exit {run.returncode}"))
		files = [f for f in os.listdir(work) if f.startswith("audio.")]
		if not run.stdout.strip() or not files:
			finish(ok=False, error=f"skipped: live or longer than {MAX_SECONDS // 60} min")
		info = json.loads(run.stdout.strip().splitlines()[-1])
		return queue_audio(os.path.join(work, files[0]), info.get("id"), info.get("title"),
			info.get("webpage_url", url))


def fetch_file(path, title):
	"""Audio file already in QUEUE_DIR (e.g. saved there by Pandora) -> queue entry; the original is removed."""
	if not os.path.exists(path):
		finish(ok=False, error=f"no such file: {path}")
	try:
		return queue_audio(path, None, title or os.path.splitext(os.path.basename(path))[0], None)
	finally:
		if os.path.exists(path):
			os.remove(path)


MODEL_URL = "https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
MODEL_PATH = os.path.join(os.path.expanduser("~"), "piano_transcription_inference_data",
	"note_F1=0.9677_pedal_F1=0.9186.pth")


def ensure_model():
	"""The package downloads its checkpoint with wget, which the image doesn't have: fetch it once here."""
	if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 160_000_000:
		return
	import urllib.request
	os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
	tmp = MODEL_PATH + ".part"
	urllib.request.urlretrieve(MODEL_URL, tmp)
	os.replace(tmp, MODEL_PATH)


def transcribe(wav, midi_path, device=None):
	"""Raw transcription (one track, all notes) of a 16 kHz mono WAV. Also used by tools/piano-convert.py."""
	import librosa
	import torch
	from piano_transcription_inference import PianoTranscription
	ensure_model()
	device = device or ("cuda" if torch.cuda.is_available() else "cpu")
	# The package's own load_audio() breaks with current librosa
	audio, _ = librosa.load(wav, sr=SAMPLE_RATE, mono=True)
	PianoTranscription(device=device, checkpoint_path=MODEL_PATH).transcribe(audio, midi_path)


def transcribe_fast(wav, midi_path):
	"""Spotify basic-pitch: ~20x faster on a Pi, but misses and invents more notes. A quick draft."""
	from basic_pitch.inference import predict
	_, midi, _ = predict(wav)
	midi.write(midi_path)


# The EZ-310 has 61 keys, C2 (36) to C7 (96). Notes outside can't light, so a wait mode would hang on them.
LOWEST, HIGHEST = 36, 96
# Transcription noise: blips shorter than this or quieter than this velocity are dropped
MIN_SECONDS = 0.05
MIN_VELOCITY = 20


def fold(note):
	"""Move a note by octaves into the keyboard's range."""
	while note < LOWEST:
		note += 12
	while note > HIGHEST:
		note -= 12
	return note


def read_notes(src):
	"""Notes as dicts (start/end in ticks and seconds) plus the other messages at absolute ticks."""
	tempo = 500000
	tick = 0
	seconds = 0.0
	sounding = {}
	notes, others = [], []

	def close(note):
		n = sounding.pop(note, None)
		if n:
			n["end"], n["end_s"] = tick, seconds
			notes.append(n)

	for msg in mido.merge_tracks(src.tracks):
		tick += msg.time
		seconds += mido.tick2second(msg.time, src.ticks_per_beat, tempo)
		if msg.type == "set_tempo":
			tempo = msg.tempo
		if msg.type == "note_on" and msg.velocity > 0:
			close(msg.note)
			sounding[msg.note] = {"note": msg.note, "velocity": msg.velocity, "start": tick, "start_s": seconds}
		elif msg.type in ("note_on", "note_off"):
			close(msg.note)
		elif msg.type != "end_of_track":
			others.append((tick, msg))
	for note in list(sounding):
		close(note)
	return notes, others, tick


def clean(notes):
	"""Drop noise, fold into the keyboard's range, assign hands and fix overlaps folding creates.
	Returns (notes, dropped, folded)."""
	kept, dropped, folded = [], 0, 0
	for n in notes:
		if n["end_s"] - n["start_s"] < MIN_SECONDS or n["velocity"] < MIN_VELOCITY:
			dropped += 1
			continue
		# The hand comes from the original pitch: a low bass note folded up is still left hand
		n["channel"] = RIGHT if n["note"] >= SPLIT_NOTE else LEFT
		key = fold(n["note"])
		if key != n["note"]:
			folded += 1
			n["note"] = key
		kept.append(n)

	# The same key twice at once on one channel (e.g. C1 folded onto a C2 that is already playing):
	# keep the louder one at the same start, otherwise end the earlier note when the next one starts.
	kept.sort(key=lambda n: (n["channel"], n["note"], n["start"], -n["velocity"]))
	result = []
	for n in kept:
		prev = result[-1] if result else None
		if prev and (prev["channel"], prev["note"]) == (n["channel"], n["note"]) and n["start"] < prev["end"]:
			if n["start"] == prev["start"]:
				dropped += 1
				continue
			prev["end"] = n["start"]
		result.append(n)
	return result, dropped, folded


def split_hands(raw_path, out_path):
	"""One track (SMF format 0) with notes split by pitch onto the two light channels, folded into
	the EZ-310's range and cleaned of transcription noise. Returns (notes, seconds, dropped, folded)."""
	src = mido.MidiFile(raw_path)
	notes, others, last_tick = read_notes(src)
	notes, dropped, folded = clean(notes)

	# (tick, order, message): at the same tick note-offs go first, then controllers, then note-ons
	events = []
	for tick, msg in others:
		if msg.type == "control_change":
			# Sustain pedal etc. applies to both hands
			events.append((tick, 1, msg.copy(channel=RIGHT)))
			events.append((tick, 1, msg.copy(channel=LEFT)))
		else:
			events.append((tick, 1, msg))
	for n in notes:
		events.append((n["start"], 2, mido.Message("note_on", channel=n["channel"], note=n["note"], velocity=n["velocity"])))
		events.append((n["end"], 0, mido.Message("note_off", channel=n["channel"], note=n["note"], velocity=0)))
	events.sort(key=lambda e: (e[0], e[1]))

	track = mido.MidiTrack()
	now = 0
	for tick, _, msg in events:
		track.append(msg.copy(time=tick - now))
		now = tick
	track.append(mido.MetaMessage("end_of_track", time=max(0, last_tick - now)))
	out = mido.MidiFile(type=0, ticks_per_beat=src.ticks_per_beat)
	out.tracks.append(track)
	out.save(out_path)
	return len(notes), round(out.length), dropped, folded


def remove_queued(qid):
	for suffix in (".wav", ".raw.mid"):
		path = os.path.join(QUEUE_DIR, qid + suffix)
		if os.path.exists(path):
			os.remove(path)
	save_index(QUEUE_INDEX, [e for e in load_index(QUEUE_INDEX) if e["id"] != qid])


def finish_song(entry, raw_path, source):
	"""Raw transcription of a queued item -> cleaned two-hand song in the library; the queue item goes."""
	name = f"{slug(entry['title'])}-{entry['id']}.mid"
	notes, seconds, dropped, folded = split_hands(raw_path, os.path.join(SONGS_DIR, name))
	song = {"file": name, "title": entry["title"], "url": entry.get("url"), "seconds": seconds,
		"notes": notes, "added": now(), "source": source}
	upsert(SONGS_INDEX, song, "file")
	remove_queued(entry["id"])
	finish(ok=True, dropped=dropped, folded=folded, **song)


def convert(qid, fast=False):
	entry = queued(qid)
	with tempfile.TemporaryDirectory() as work:
		raw = os.path.join(work, "raw.mid")
		(transcribe_fast if fast else transcribe)(os.path.join(QUEUE_DIR, qid + ".wav"), raw)
		finish_song(entry, raw, "pi-fast" if fast else "pi")


def register(qid):
	entry = queued(qid)
	raw = os.path.join(QUEUE_DIR, qid + ".raw.mid")
	if not os.path.exists(raw):
		finish(ok=False, error=f"missing {raw}")
	finish_song(entry, raw, "computer")


def add_midi(path, title):
	"""A ready-made MIDI file (sent to Pandora) -> songs, unchanged."""
	try:
		midi = mido.MidiFile(path)
	except Exception as e:
		finish(ok=False, error=f"not a MIDI file: {e}")
	notes = sum(1 for track in midi.tracks for m in track if m.type == "note_on" and m.velocity > 0)
	channels = sorted({m.channel + 1 for track in midi.tracks for m in track if m.type == "note_on"})
	name = f"{slug(title)}-f{int(time.time())}.mid"
	shutil.copyfile(path, os.path.join(SONGS_DIR, name))
	song = {"file": name, "title": title, "url": None, "seconds": round(midi.length), "notes": notes,
		"added": now(), "source": "telegram"}
	upsert(SONGS_INDEX, song, "file")
	finish(ok=True, channels=channels, **song)


def main():
	os.makedirs(SONGS_DIR, exist_ok=True)
	os.makedirs(QUEUE_DIR, exist_ok=True)
	args = sys.argv[1:]
	command = args[0] if args else ""
	if command == "fetch" and len(args) in (2, 3):
		target, title = args[1], (args[2] if len(args) == 3 else None)
		entry = fetch_url(target) if re.match(r"https?://|ytsearch", target) else fetch_file(target, title)
		finish(ok=True, **entry)
	elif command == "convert" and len(args) in (2, 3) and args[2:] in ([], ["fast"]):
		convert(args[1], fast=args[2:] == ["fast"])
	elif command == "register" and len(args) == 2:
		register(args[1])
	elif command == "add-midi" and len(args) == 3:
		add_midi(args[1], args[2])
	elif command == "drop" and len(args) == 2:
		queued(args[1])
		remove_queued(args[1])
		finish(ok=True, id=args[1])
	elif len(args) == 1 and re.match(r"https?://|ytsearch", command):
		convert(fetch_url(command)["id"])
	else:
		finish(ok=False, error=__doc__.strip())


if __name__ == "__main__":
	main()
