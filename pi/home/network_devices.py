import json
import os
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Writes the Homepage "Devices on network" tile data (run every 10 minutes by cron).
# Names: optional ~/network_names.json, {"aa:bb:cc:dd:ee:ff": "Living room TV"} (stays on the Pi).
HOME = "/home/lbx"
OUT = f"{HOME}/server/homepage/public/devices.json"
NAMES = f"{HOME}/network_names.json"
CACHE = f"{HOME}/cache"
MANUF = f"{CACHE}/manuf"
SEEN = f"{CACHE}/network_seen.json"
MANUF_URL = "https://www.wireshark.org/download/automated/data/manuf"
IFACE = "eth0"
NEW_FOR = 24 * 3600


def run(cmd):
	try:
		return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, text=True).stdout
	except Exception:
		return ""


def load(path, default):
	try:
		with open(path) as f:
			return json.load(f)
	except Exception:
		return default


def save(path, data):
	with open(path + ".tmp", "w") as f:
		json.dump(data, f, ensure_ascii=False)
	os.replace(path + ".tmp", path)


def own_address():
	info = json.loads(run(["ip", "-j", "-4", "addr", "show", IFACE]))[0]["addr_info"][0]
	with open(f"/sys/class/net/{IFACE}/address") as f:
		mac = f.read().strip()
	return mac, info["local"], info["prefixlen"]


def gateway():
	routes = json.loads(run(["ip", "-j", "-4", "route", "show", "default"]) or "[]")
	return routes[0].get("gateway") if routes else None


def sweep(ip, prefix):
	# Pinging every address makes each live host answer ARP, even if it drops the ping itself
	if prefix < 22:
		return
	base = int.from_bytes(bytes(map(int, ip.split("."))), "big") & ~((1 << (32 - prefix)) - 1)
	hosts = [".".join(str(b) for b in (base + i).to_bytes(4, "big")) for i in range(1, (1 << (32 - prefix)) - 1)]
	with ThreadPoolExecutor(64) as pool:
		list(pool.map(lambda h: run(["ping", "-c1", "-W1", h]), hosts))


def neighbours():
	found = {}
	for n in json.loads(run(["ip", "-j", "-4", "neigh", "show", "dev", IFACE]) or "[]"):
		states = n.get("state", [])
		if n.get("lladdr") and not {"FAILED", "INCOMPLETE"} & set(states):
			found[n["lladdr"].lower()] = n["dst"]
	return found


def vendors():
	# Wireshark's MAC vendor list, refreshed monthly
	if not os.path.exists(MANUF) or time.time() - os.path.getmtime(MANUF) > 30 * 86400:
		try:
			with urllib.request.urlopen(MANUF_URL, timeout=30) as r:
				data = r.read()
			with open(MANUF + ".tmp", "wb") as f:
				f.write(data)
			os.replace(MANUF + ".tmp", MANUF)
		except Exception:
			pass
	table = {}
	try:
		with open(MANUF, encoding="utf-8", errors="replace") as f:
			for line in f:
				parts = line.rstrip("\n").split("\t")
				if len(parts) >= 2 and not line.startswith("#") and "/" not in parts[0]:
					table[parts[0].strip().lower()] = parts[1].strip()
	except OSError:
		pass
	return table


def describe(mac, names, table):
	if mac in names:
		return names[mac]
	if int(mac[:2], 16) & 2:
		return "Private MAC (phone/laptop)"
	vendor = table.get(mac[:8])
	return f"{vendor} {mac[9:]}" if vendor else mac


def main():
	os.makedirs(CACHE, exist_ok=True)
	my_mac, my_ip, prefix = own_address()
	sweep(my_ip, prefix)
	found = neighbours()
	found[my_mac.lower()] = my_ip

	now = time.time()
	first_run = not os.path.exists(SEEN)
	seen = load(SEEN, {})
	names = {k.lower(): v for k, v in load(NAMES, {}).items()}
	names.setdefault(my_mac.lower(), "homepi (this Pi)")
	table = vendors()
	gw = gateway()
	for mac, ip in found.items():
		if ip == gw:
			names.setdefault(mac, f"Router ({describe(mac, {}, table)})")

	devices = []
	for mac, ip in found.items():
		# Devices present on the very first scan don't count as new
		first = seen.setdefault(mac, 0 if first_run else now)
		name = describe(mac, names, table)
		if now - first < NEW_FOR:
			name = "🆕 " + name
		devices.append({"name": name, "ip": ip, "mac": mac})
	save(SEEN, seen)
	devices.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))

	summary = {"name": f"{len(devices)} online", "ip": "updated " + time.strftime("%H:%M"), "mac": ""}
	save(OUT, {"count": len(devices), "updated": time.strftime("%Y-%m-%d %H:%M"), "devices": [summary] + devices})


if __name__ == "__main__":
	main()
