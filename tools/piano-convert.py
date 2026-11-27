#!/usr/bin/env python3
"""Convert the Pi's piano queue on this (faster) computer with the accurate ByteDance model.

  python tools/piano-convert.py              convert everything waiting in the queue, then stop
  python tools/piano-convert.py --list       only show the queue
  python tools/piano-convert.py ID ...       convert these items
  python tools/piano-convert.py --watch      keep running: convert what the tutor page or Pandora
                                             sends "to a computer" (🎯 Accurate / 💻), checking every 30 s
  python tools/piano-convert.py --install    start --watch at login (Windows: HKCU Run key, macOS:
                                             LaunchAgent) and start it now; --uninstall undoes it

Audio waits on the Pi in ~/server/piano/queue (Pandora and the tutor page put YouTube links and audio
files there). This script copies each item here, transcribes it (NVIDIA GPU if torch has CUDA, else
CPU), sends the raw notes back, and the Pi turns them into a two-hand song for the EZ-310.
With --watch the computer checks in with the Pi (queue/.worker-<name>), so the tutor page shows it as
online; it claims one item at a time (queue/.claim-<id>), and a failure is left in <id>.computer-error.

The first run creates its own Python environment in ~/.cache/piano-convert (about 1 GB with torch)
and downloads the model (~165 MB). Needs Python 3.10+ and ssh key login to the Pi (as for deploy.sh).
Set PIANO_PI to use another host than the default (PIANO_REMOTE: another folder on it),
PIANO_WORKER_NAME to name this computer on the tutor page. Log of --watch: ~/.cache/piano-convert/worker.log
"""
import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import venv

PI = os.environ.get("PIANO_PI", "lbx@homepi.tail04e168.ts.net")
# Folder of the piano compose project on the Pi, relative to the ssh user's home
REMOTE = os.environ.get("PIANO_REMOTE", "server/piano")
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.abspath(__file__)
TRANSCRIBE_DIR = os.path.normpath(os.path.join(HERE, "..", "pi", "home", "server", "piano"))
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "piano-convert")
ENV_DIR = os.path.join(CACHE, "venv")
LOG = os.path.join(CACHE, "worker.log")
# piano_transcription_inference imports audioread without declaring it
PACKAGES = ["torch", "piano_transcription_inference", "librosa", "audioread", "mido"]
POLL = 30
NAME = re.sub(r"[^A-Za-z0-9_-]", "-", os.environ.get("PIANO_WORKER_NAME") or socket.gethostname().split(".")[0])[:40]
# No console windows popping up when --watch runs in the background on Windows
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/lu.piano-convert.plist")
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"

# Runs on the Pi (python3 -) for --watch: heartbeat, claims, results. argv: action, folder, args...
REMOTE_SCRIPT = r'''
import json, os, sys, time
action, folder = sys.argv[1], os.path.expanduser(sys.argv[2])
queue_dir = os.path.join(folder, "queue")
ONLINE = 120

def load(path, default):
	try:
		with open(path) as f:
			return json.load(f)
	except (OSError, ValueError):
		return default

def save(path, data):
	tmp = f"{path}.{os.getpid()}.tmp"
	with open(tmp, "w") as f:
		json.dump(data, f, ensure_ascii=False, indent=1)
	os.replace(tmp, path)

def seen_workers():
	out = {}
	for n in os.listdir(queue_dir):
		if n.startswith(".worker-"):
			w = load(os.path.join(queue_dir, n), {})
			out[w.get("name", n[8:])] = w.get("seen", 0)
	return out

def claims():
	out = {}
	for n in os.listdir(queue_dir):
		if n.startswith(".claim-"):
			try:
				out[n[7:]] = open(os.path.join(queue_dir, n)).read().strip()
			except OSError:
				pass
	return out

def forget(qid):
	requests = load(os.path.join(queue_dir, "computer.json"), {})
	if qid in requests:
		del requests[qid]
		save(os.path.join(queue_dir, "computer.json"), requests)
	try:
		os.remove(os.path.join(queue_dir, ".claim-" + qid))
	except FileNotFoundError:
		pass

if action == "status":
	name, device, busy = sys.argv[3:6]
	save(os.path.join(queue_dir, ".worker-" + name),
		{"name": name, "device": device, "busy": busy or None, "seen": time.time()})
	print(json.dumps({"queue": load(os.path.join(queue_dir, "queue.json"), []),
		"requests": load(os.path.join(queue_dir, "computer.json"), {}),
		"claims": claims(), "workers": seen_workers(), "now": time.time()}))
elif action == "claim":
	name, qid = sys.argv[3:5]
	path = os.path.join(queue_dir, ".claim-" + qid)
	holder = claims().get(qid)
	if holder and holder != name and time.time() - seen_workers().get(holder, 0) < ONLINE:
		print(json.dumps({"ok": False, "holder": holder}))
	else:
		with open(path, "w") as f:
			f.write(name)
		print(json.dumps({"ok": True}))
elif action == "done":
	forget(sys.argv[3])
	print(json.dumps({"ok": True}))
elif action == "fail":
	qid, error = sys.argv[3], sys.argv[4]
	with open(os.path.join(queue_dir, qid + ".computer-error"), "w") as f:
		f.write(error)
	forget(qid)
	print(json.dumps({"ok": True}))
elif action == "release":
	name = sys.argv[3]
	for qid, holder in claims().items():
		if holder == name:
			os.remove(os.path.join(queue_dir, ".claim-" + qid))
	print(json.dumps({"ok": True}))
elif action == "offline":
	try:
		os.remove(os.path.join(queue_dir, ".worker-" + sys.argv[3]))
	except FileNotFoundError:
		pass
	print(json.dumps({"ok": True}))
'''


def say(*args):
	print(time.strftime("%H:%M:%S"), *args, flush=True)


def env_python(windowless=False):
	if os.name == "nt":
		return os.path.join(ENV_DIR, "Scripts", "pythonw.exe" if windowless else "python.exe")
	return os.path.join(ENV_DIR, "bin", "python")


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
	sys.exit(subprocess.run([python, SCRIPT, *sys.argv[1:]]).returncode)


def ssh(command, stdin=None):
	return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", PI, command],
		input=stdin, capture_output=True, text=True, creationflags=NO_WINDOW, timeout=600)


def scp(src, dest):
	subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", src, dest], check=True,
		capture_output=True, creationflags=NO_WINDOW, timeout=600)


def remote(action, *args):
	"""Run REMOTE_SCRIPT on the Pi; returns its JSON answer (raises if the Pi can't be reached)."""
	import shlex
	command = "python3 - " + " ".join(shlex.quote(a) for a in (action, REMOTE, *args))
	result = ssh(command, REMOTE_SCRIPT)
	if result.returncode != 0:
		raise RuntimeError(f"Pi: {(result.stderr or result.stdout).strip()[-300:]}")
	return json.loads(result.stdout.strip().splitlines()[-1])


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
		say(f"  transcribed in {time.time() - start:.0f}s")
		scp(raw, f"{PI}:{REMOTE}/queue/{qid}.raw.mid")
	result = ssh(f"cd {REMOTE} && docker compose run --rm transcriber register {qid}")
	lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
	return json.loads(lines[-1]) if lines else {"ok": False, "error": result.stderr.strip()[-300:]}


def device_label():
	import torch
	if torch.cuda.is_available():
		return torch.cuda.get_device_name(0)
	return f"{platform.system().replace('Darwin', 'Mac')} CPU"


# --- --watch -----------------------------------------------------------------------------------

def single_instance():
	"""Only one watcher per computer: an exclusive lock on a file in the cache folder."""
	os.makedirs(CACHE, exist_ok=True)
	lock = open(os.path.join(CACHE, "worker.lock"), "a+")
	try:
		if os.name == "nt":
			import msvcrt
			msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
		else:
			import fcntl
			fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
	except OSError:
		sys.exit("Another piano-convert --watch is already running on this computer.")
	return lock


def watch():
	lock = single_instance()  # noqa: F841  (held until exit)
	if sys.stdout is None or not sys.stdout.isatty():   # background: write a log (pythonw has no stdout)
		log = open(LOG, "a", buffering=1, encoding="utf-8")
		sys.stdout = sys.stderr = log
	device = device_label()
	say(f"watching {PI} as {NAME} ({device}), every {POLL}s")
	state = {"busy": ""}

	def heartbeat():                     # keeps the computer "online" during a long conversion
		while True:
			time.sleep(POLL)
			if state["busy"]:
				try:
					remote("status", NAME, device, state["busy"])
				except Exception:
					pass
	threading.Thread(target=heartbeat, daemon=True).start()

	released = False
	while True:
		try:
			if not released:
				remote("release", NAME)   # claims left over from a crash or a sleep
				released = True
			info = remote("status", NAME, device, "")
		except Exception as e:
			say(f"can't reach the Pi: {e}")
			time.sleep(POLL * 2)
			continue
		queued = {i["id"]: i for i in info["queue"]}
		taken = {qid for qid, holder in info["claims"].items()
			if holder != NAME and info["now"] - info["workers"].get(holder, 0) < 120}
		todo = sorted((t, qid) for qid, t in info["requests"].items() if qid in queued and qid not in taken)
		if not todo:
			time.sleep(POLL)
			continue
		item = queued[todo[0][1]]
		try:
			if not remote("claim", NAME, item["id"]).get("ok"):
				continue
		except Exception as e:
			say(f"claim failed: {e}")
			continue
		state["busy"] = item["id"]
		say(f"converting {item.get('title')} ({length(item.get('seconds'))})")
		try:
			result = convert(item)
		except Exception as e:
			result = {"ok": False, "error": str(e)}
		state["busy"] = ""
		try:
			if result.get("ok"):
				say(f"  done: {result['file']}, {result['notes']} notes")
				remote("done", item["id"])
			else:
				say(f"  failed: {result.get('error')}")
				remote("fail", item["id"], str(result.get("error"))[:500])
		except Exception as e:
			say(f"  couldn't report back: {e}")


# --- --install / --uninstall --------------------------------------------------------------------

def install():
	run_in_env()                         # makes sure the environment exists (re-runs this inside it)
	if os.name == "nt":
		command = f'"{env_python(windowless=True)}" "{SCRIPT}" --watch'
		subprocess.run(["reg", "add", RUN_KEY, "/v", "PianoConvert", "/t", "REG_SZ", "/d", command, "/f"],
			check=True, capture_output=True)
		subprocess.Popen([env_python(windowless=True), SCRIPT, "--watch"], creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS,
			close_fds=True)
		print(f"Installed: starts at login (registry Run key 'PianoConvert') and is running now.\nLog: {LOG}")
	elif sys.platform == "darwin":
		os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
		with open(LAUNCH_AGENT, "w") as f:
			f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
	<key>Label</key><string>lu.piano-convert</string>
	<key>ProgramArguments</key><array><string>{env_python()}</string><string>{SCRIPT}</string><string>--watch</string></array>
	<key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
	<key>StandardOutPath</key><string>{LOG}</string>
	<key>StandardErrorPath</key><string>{LOG}</string>
</dict></plist>
""")
		subprocess.run(["launchctl", "unload", LAUNCH_AGENT], capture_output=True)
		subprocess.run(["launchctl", "load", "-w", LAUNCH_AGENT], check=True)
		print(f"Installed: LaunchAgent {LAUNCH_AGENT} (starts at login, running now).\nLog: {LOG}")
	else:
		sys.exit("--install knows Windows and macOS; on Linux run --watch from a systemd user unit.")


def uninstall():
	if os.name == "nt":
		subprocess.run(["reg", "delete", RUN_KEY, "/v", "PianoConvert", "/f"], capture_output=True)
		subprocess.run(["powershell", "-NoProfile", "-Command",
			"Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*piano-convert.py*--watch*' } "
			"| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
	elif sys.platform == "darwin":
		subprocess.run(["launchctl", "unload", "-w", LAUNCH_AGENT], capture_output=True)
		if os.path.exists(LAUNCH_AGENT):
			os.remove(LAUNCH_AGENT)
	try:
		remote("offline", NAME)
	except Exception:
		pass
	print("Uninstalled: the watcher is stopped and won't start at login.")


def main():
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("ids", nargs="*", help="queue items to convert (default: all)")
	parser.add_argument("--list", action="store_true", help="only show the queue")
	parser.add_argument("--watch", action="store_true", help="keep converting what is sent to a computer")
	parser.add_argument("--install", action="store_true", help="run --watch at login, starting now")
	parser.add_argument("--uninstall", action="store_true", help="stop --watch and remove it from login")
	args = parser.parse_args()

	if args.uninstall:
		return uninstall()
	if args.install:
		return install()
	if args.watch:
		run_in_env()
		return watch()

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
			try:
				remote("done", item["id"])    # in case it was also waiting for a computer
			except Exception:
				pass
		else:
			failed += 1
			print(f"  failed: {result.get('error')}")
	sys.exit(1 if failed else 0)


if __name__ == "__main__":
	main()
