import datetime
import json
import os

# Plan limits come from CodexBar on the Pi (fetch_claude_limits.py); Mac cost
# and tokens are pushed from the Mac (~/dev/raspberry-projects/claude-usage-push)
HERE = os.path.dirname(os.path.abspath(__file__))
PI_PATH = os.path.join(HERE, "claude_limits_pi.json")
MAC_PATH = os.path.join(HERE, "claude_usage.json")
DAYS = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]


def read(path):
	try:
		with open(path) as f:
			return json.load(f)
	except (FileNotFoundError, ValueError):
		return None


def load():
	mac = read(MAC_PATH)
	pi = read(PI_PATH)
	if not mac and not pi:
		return None
	# Limits from the Pi when available (always current), Mac push as fallback
	merged = dict(mac or {"today_cost": 0, "today_tokens": 0, "cost_30d": 0, "tokens_30d": 0})
	merged.update(pi or {})
	merged["mac_updated"] = (mac or {}).get("updated", "-")
	return merged


def reset_time(iso):
	when = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
	if when.date() == datetime.date.today():
		return when.strftime("%H:%M")
	return f"{DAYS[when.weekday()]} {when.strftime('%H:%M')}"


def tokens(n):
	return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


def summary():
	u = load()
	if not u:
		return {"claude_limits_updated": "waiting for Mac", "claude_week_left": "-", "claude_5h_left": "-"}
	return {
		"claude_limits_updated": u["updated"],
		"claude_plan": u.get("plan") or "-",
		"claude_5h_left": f"{100 - u['session_used']}%",
		"claude_5h_reset": reset_time(u["session_resets_at"]),
		"claude_week_left": f"{100 - u['week_used']}%",
		"claude_week_reset": reset_time(u["week_resets_at"]),
		"claude_week_pace": (u.get("week_pace") or "-").split(" | ")[0],
		"claude_mac_today": f"${u['today_cost']:.2f} · {tokens(u['today_tokens'])} tokens",
		"claude_mac_30d": f"${u['cost_30d']:.2f} · {tokens(u['tokens_30d'])} tokens",
		"claude_mac_updated": u["mac_updated"],
		"claude_top_model": u.get("top_model") or "-",
	}
