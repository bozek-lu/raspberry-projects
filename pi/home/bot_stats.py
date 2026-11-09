import datetime
import json
import os

# Daily activity counters for the dashboard, kept next to the bot
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandora_stats.json")


def load():
	try:
		with open(PATH) as f:
			return json.load(f)
	except FileNotFoundError:
		return {"days": {}, "total": 0, "last_activity": None, "last_morning": None}


def save(data):
	with open(PATH + ".tmp", "w") as f:
		json.dump(data, f, indent=1)
	os.replace(PATH + ".tmp", PATH)


def kind_of(text):
	if text.startswith("?"):
		return "claude"
	if text.startswith("!"):
		return "shell"
	if text.startswith("/"):
		return "commands"
	return "fun"


def day_entry(data, date):
	day = data["days"].setdefault(date.isoformat(), {})
	for key in ("messages", "sent", "claude", "shell", "commands", "fun", "claude_calls", "claude_tokens", "claude_cost"):
		day.setdefault(key, 0)
	day.setdefault("users", [])
	return day


def record(user_id, text):
	data = load()
	now = datetime.datetime.now()
	day = day_entry(data, now.date())
	day["messages"] += 1
	day[kind_of(text)] += 1
	if user_id not in day["users"]:
		day["users"].append(user_id)
	data["total"] += 1
	data["last_activity"] = now.strftime("%d.%m %H:%M")
	# Keep 30 days of history
	data["days"] = dict(sorted(data["days"].items())[-30:])
	save(data)


def record_sent():
	# Every message Pandora sends: replies, morning message, photos, dice
	data = load()
	day_entry(data, datetime.date.today())["sent"] += 1
	data["total_sent"] = data.get("total_sent", 0) + 1
	save(data)


def record_claude(tokens, cost):
	# Every Claude call (questions and morning tips); cost is the API-equivalent value
	data = load()
	day = day_entry(data, datetime.date.today())
	day["claude_calls"] += 1
	day["claude_tokens"] += tokens
	day["claude_cost"] += cost
	save(data)


def claude_usage(date):
	day = load()["days"].get(date.isoformat(), {})
	return day.get("claude_calls", 0), day.get("claude_tokens", 0), day.get("claude_cost", 0)


def record_morning():
	data = load()
	data["last_morning"] = datetime.datetime.now().strftime("%d.%m %H:%M")
	save(data)


def today():
	data = load()
	day = data["days"].get(datetime.date.today().isoformat(), {})
	week = sorted(data["days"].items())[-7:]
	month = [d for date, d in data["days"].items() if date[:7] == datetime.date.today().isoformat()[:7]]
	return {
		"claude_calls_today": day.get("claude_calls", 0),
		"claude_tokens_today": f"{day.get('claude_tokens', 0):,}".replace(",", " "),
		"claude_cost_today": f"${day.get('claude_cost', 0):.2f}",
		"claude_calls_week": sum(d.get("claude_calls", 0) for _, d in week),
		"claude_cost_month": f"${sum(d.get('claude_cost', 0) for d in month):.2f}",
		"messages_today": day.get("messages", 0),
		"claude_today": day.get("claude", 0),
		"users_today": len(day.get("users", [])),
		"messages_week": sum(d["messages"] for _, d in week),
		"messages_total": data["total"],
		"sent_today": day.get("sent", 0),
		"sent_week": sum(d.get("sent", 0) for _, d in week),
		"sent_total": data.get("total_sent", 0),
		"last_activity": data["last_activity"] or "-",
		"last_morning": data["last_morning"] or "-",
	}
