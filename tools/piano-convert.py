#!/usr/bin/env python3
"""Convert the Pi's piano queue on this (faster) computer with the accurate ByteDance model.

  python tools/piano-convert.py           convert everything waiting in the queue
  python tools/piano-convert.py --list    only show the queue
  python tools/piano-convert.py ID ...    convert these items

Audio waits on the Pi in ~/server/piano/queue (Pandora puts YouTube links and audio files there).
This script copies each item here, transcribes it (GPU if there is an NVIDIA one, else CPU), sends
the raw notes back, and the Pi turns them into a two-hand song for the EZ-310; Pandora then sends it
to you on Telegram.

The first run creates its own Python environment in ~/.cache/piano-convert (about 1 GB with torch)
and downloads the model (~165 MB). Needs Python 3.10+ and ssh key login to the Pi (as for deploy.sh).
Set PIANO_PI to use another host than the default (PIANO_REMOTE: another folder on it).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import venv

PI = os.environ.get("PIANO_PI", "lbx@homepi.tail04e168.ts.net")
# Folder of the piano compose project on the Pi, relative to the ssh user's home
REMOTE = os.environ.get("PIANO_REMOTE", "server/piano")
HERE = os.path.dirname(os.path.abspath(__file__))
TRANSCRIBE_DIR = os.path.normpath(os.path.join(HERE, "..", "pi", "home", "server", "piano"))
ENV_DIR = os.path.join(os.path.expanduser("~"), ".cache", "piano-convert", "venv")
# piano_transcription_inference imports audioread without declaring it
PACKAGES = ["torch", "piano_transcription_inference", "librosa", "audioread", "mido"]


def env_python():
	return os.path.join(ENV_DIR, "Scripts", "python.exe") if os.name == "nt" else os.path.join(ENV_DIR, "bin", "python")


def run_in_env():
	"""Re-run this script with its own environment (created on first use)."""
	if os.path.normcase(os.path.abspath(sys.prefix)) == os.path.normcase(os.path.abspath(ENV_DIR)):
		return
	python = env_python()
	marker = os.path.join(ENV_DIR, "piano-packages.txt")
	wanted = " ".join(PACKAGES)
	installed = open(marker).read() if os.path.exists(marker) else None
	if installed != wanted:
		if not os.path.exists(python):
			print(f"First run: setting up {ENV_DIR} (torch + piano_transcription_inference, a few minutes)...", flush=True)
			venv.create(ENV_DIR, with_pip=True)
			subprocess.run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
		else:
			print("Updating the Python environment...", flush=True)
		subprocess.run([python, "-m", "pip", "install", "-q", *PACKAGES], check=True)
		with open(marker, "w") as f:
			f.write(wanted)
	sys.exit(subprocess.run([python, os.path.abspath(__file__), *sys.argv[1:]]).returncode)


def ssh(command):
	return subprocess.run(["ssh", "-o", "BatchMode=yes", PI, command], capture_output=True, text=True)


def scp(src, dest):
	subprocess.run(["scp", "-q", "-o", "BatchMode=yes", src, dest], check=True)


def remote_queue():
	result = ssh(f"cat {REMOTE}/queue/queue.json 2>/dev/null || echo []")
	if result.returncode != 0:
		sys.exit(f"Can't reach the Pi ({PI}): {result.stderr.strip()}")
	return json.loads(result.stdout or "[]")


def length(seconds):
	return "%d:%02d" % divmod(int(seconds or 0), 60)


def convert(item):
	sys.path.insert(0, TRANSCRIBE_DIR)
	import transcribe  # the Pi's transcriber: same model and checkpoint

	qid = item["id"]
	with tempfile.TemporaryDirectory() as work:
		wav, raw = os.path.join(work, "audio.wav"), os.path.join(work, "raw.mid")
		scp(f"{PI}:{REMOTE}/queue/{qid}.wav", wav)
		start = time.time()
		transcribe.transcribe(wav, raw)
		print(f"  transcribed in {time.time() - start:.0f}s", flush=True)
		scp(raw, f"{PI}:{REMOTE}/queue/{qid}.raw.mid")
	result = ssh(f"cd {REMOTE} && docker compose run --rm transcriber register {qid}")
	lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
	return json.loads(lines[-1]) if lines else {"ok": False, "error": result.stderr.strip()[-300:]}


def main():
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("ids", nargs="*", help="queue items to convert (default: all)")
	parser.add_argument("--list", action="store_true", help="only show the queue")
	args = parser.parse_args()

	items = remote_queue()
	if args.ids:
		items = [i for i in items if i["id"] in args.ids]
	if not items or args.list:
		print(f"Queue on {PI}: {len(items)} item(s)")
		for i in items:
			print(f"  {i['id']:<14} {length(i.get('seconds')):>6}  {i.get('title')}")
		return

	run_in_env()
	failed = 0
	for n, item in enumerate(items, 1):
		print(f"[{n}/{len(items)}] {item.get('title')} ({length(item.get('seconds'))})", flush=True)
		try:
			result = convert(item)
		except Exception as e:
			result = {"ok": False, "error": str(e)}
		if result.get("ok"):
			print(f"  done: {result['file']}, {result['notes']} notes (Pandora will send it on Telegram)")
		else:
			failed += 1
			print(f"  failed: {result.get('error')}")
	sys.exit(1 if failed else 0)


if __name__ == "__main__":
	main()
