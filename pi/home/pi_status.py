import json
import os
import shutil
import subprocess
import sys
import time


def run(cmd):
	try:
		return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, text=True).stdout.strip()
	except Exception:
		return ""


def base_url():
	# Tailscale name works from any device on the tailnet; fall back to the LAN address
	try:
		host = json.loads(run(["tailscale", "status", "--json"]))["Self"]["DNSName"].rstrip(".")
	except Exception:
		host = run(["hostname", "-I"]).split(" ")[0]
	return f"http://{host}"


def links():
	url = base_url()
	return {
		"Dashboard": url,
		"Home Assistant": f"{url}:8123",
		"AdGuard Home": f"{url}:3000",
		"Portainer": f"{url}:9000",
	}


def power():
	# Bits from vcgencmd get_throttled: 0x1 undervoltage now, 0x10000 undervoltage since boot
	try:
		flags = int(run(["vcgencmd", "get_throttled"]).split("=")[1], 16)
	except Exception:
		return "unknown"
	if flags & 0x1:
		return "Undervoltage now"
	if flags & 0x10000:
		return "Undervoltage since boot"
	return "OK"


def status():
	temp = run(["vcgencmd", "measure_temp"]).replace("temp=", "").replace("'C", " °C")
	uptime = run(["uptime", "-p"]).replace("up ", "")
	disk = shutil.disk_usage("/")
	names = run(["docker", "ps", "-a", "--format", "{{.Names}} {{.State}}"]).splitlines()
	running = [n.split()[0] for n in names if n.endswith(" running")]
	return {
		"temp": temp,
		"power": power(),
		"uptime": uptime,
		"disk_free": f"{disk.free / 1e9:.1f} GB",
		"containers": f"{len(running)}/{len(names)} running",
		"stopped": [n.split()[0] for n in names if not n.endswith(" running")],
		"bot": run(["systemctl", "is-active", "telebot"]),
		"updated": time.strftime("%H:%M"),
	}


def bot_status():
	import access
	import bot_stats
	import claude_limits

	people = access.load()
	try:
		with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandora_reminders.json")) as f:
			reminders = len(json.load(f))
	except FileNotFoundError:
		reminders = 0
	return {
		**bot_stats.today(),
		**claude_limits.summary(),
		"users_allowed": len(people["allowed"]),
		"users_pending": len(people["pending"]),
		"reminders": reminders,
	}


if __name__ == "__main__":
	# Write status JSON for the dashboard; replace atomically so it is never read half-written
	import weather
	path = sys.argv[1]
	with open(path + ".tmp", "w") as f:
		json.dump({**status(), **bot_status(), **weather.dashboard()}, f)
	os.replace(path + ".tmp", path)
