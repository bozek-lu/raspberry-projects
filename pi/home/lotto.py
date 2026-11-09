import datetime
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

# Results, prizes, jackpots and statistics from LOTTO OpenAPI (developers.lotto.pl); key in ~/.env as LOTTO_API_KEY
API = "https://developers.lotto.pl/api/open/v1/lotteries/"
NUMBERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandora_lotto.json")
SPENDING_PATH = "/home/lbx/server/actual/bank-sync/spending.json"
HOT_COLD_MONTHS = 6
# Prize tier in draw-prizes -> numbers matched
TIER_HITS = {"1": 6, "2": 5, "3": 4, "4": 3}
WEEKDAYS = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
WARSAW = ZoneInfo("Europe/Warsaw")


def _get(path, **params):
	url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
	req = urllib.request.Request(url, headers={
		"accept": "application/json", "secret": os.getenv("LOTTO_API_KEY", ""),
		# The API rejects Python's default user agent
		"User-Agent": "homepi-pandora-bot/1.0",
	})
	with urllib.request.urlopen(req, timeout=20) as r:
		return json.load(r)


def _local(utc):
	return datetime.datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(WARSAW)


def _money(value):
	if value >= 1_000_000:
		return f"{value / 1_000_000:g} mln zł".replace(".", ",")
	return f"{value:,.0f} zł".replace(",", " ")


def last_results(game):
	# {"Lotto": {...}, "LottoPlus": {...}} with date, numbers, special numbers and draw id
	return {
		r["gameType"]: {
			"id": r["drawSystemId"], "date": _local(r["drawDate"]).date(),
			"numbers": sorted(r["results"][0]["resultsJson"]), "special": r["results"][0]["specialResults"],
		}
		for r in _get("draw-results/last-results-per-game", gameType=game)
	}


def prizes(draw_id):
	# {"Lotto": {3: 35.0, 4: 255.7, ...}, "LottoPlus": {...}}
	return {
		p["gameType"]: {TIER_HITS[tier]: v["prizeValue"] for tier, v in p["prizes"].items() if tier in TIER_HITS}
		for p in _get(f"draw-prizes/Lotto/{draw_id}") if p["prizes"]
	}


def jackpot(game):
	j = _get("info/game-jackpot", gameType=game)
	return j["jackpotValue"], j.get("jackpotPlusValue"), _local(j["closestDraw"])


def hot_and_cold():
	today = datetime.date.today()
	stats = _get("draw-statistics/numbers-frequency", gameType="Lotto",
		dateFrom=(today - datetime.timedelta(days=30 * HOT_COLD_MONTHS)).isoformat(), dateTo=today.isoformat())
	freq = sorted(stats["numberFrequrency"], key=lambda n: -n["numberOfOccurrences"])
	return [n["number"] for n in freq[:6]], [n["number"] for n in freq[-6:]], stats["totalDraws"]


def my_numbers():
	try:
		with open(NUMBERS_PATH) as f:
			return json.load(f)
	except FileNotFoundError:
		return []


def save_numbers(sets):
	with open(NUMBERS_PATH + ".tmp", "w") as f:
		json.dump(sets, f)
	os.replace(NUMBERS_PATH + ".tmp", NUMBERS_PATH)


def parse_numbers(words):
	# Six different numbers from 1 to 49, or None
	try:
		numbers = sorted({int(w) for w in words})
	except ValueError:
		return None
	return numbers if len(numbers) == 6 and all(1 <= n <= 49 for n in numbers) and len(words) == 6 else None


def spending():
	try:
		with open(SPENDING_PATH) as f:
			cats = json.load(f)["categories"]
	except (FileNotFoundError, ValueError):
		return None
	return next((c for c in cats if c["category"] == "Lottery"), None)


def _fmt(nums):
	return " ".join(str(n) for n in nums)


def _day(d):
	return d["date"].strftime("%d.%m")


def _when(d):
	return f"{WEEKDAYS[d.weekday()]} {d.strftime('%H:%M')}"


def fetch():
	# Everything the morning message and the Homepage tile show; raises if the API is down
	lotto = last_results("Lotto")
	pot, pot_plus, next_lotto = jackpot("Lotto")
	euro_pot, _, next_euro = jackpot("EuroJackpot")
	return {
		"lotto": lotto, "won": prizes(lotto["Lotto"]["id"]),
		"euro": last_results("EuroJackpot").get("EuroJackpot"), "mini": last_results("MiniLotto").get("MiniLotto"),
		"pot": pot, "pot_plus": pot_plus, "next_lotto": next_lotto, "euro_pot": euro_pot, "next_euro": next_euro,
	}


def my_results(lotto, won):
	# [(set, [(label, hits, prize)], total)] for each saved set
	results = []
	for mine in my_numbers():
		games = []
		for game, label in (("Lotto", "Lotto"), ("LottoPlus", "Plus")):
			if game in lotto:
				hits = sorted(set(mine) & set(lotto[game]["numbers"]))
				games.append((label, hits, won.get(game, {}).get(len(hits), 0)))
		results.append((mine, games, sum(g[2] for g in games)))
	return results


def wins_text(won):
	return ", ".join(f"{h} traf. {_money(won['Lotto'][h])}" for h in (3, 4, 5) if h in won.get("Lotto", {}))


def message():
	try:
		d = fetch()
		hot, cold, draws = hot_and_cold()
	except Exception as e:
		print(f"Lotto error: {e}")
		return None
	lotto, won, euro, mini = d["lotto"], d["won"], d["euro"], d["mini"]
	draw, plus = lotto["Lotto"], lotto.get("LottoPlus")
	lines = [f"🎱 Lotto {_day(draw)}: {_fmt(draw['numbers'])}" + (f" · Plus: {_fmt(plus['numbers'])}" if plus else "")]
	if "Lotto" in won:
		lines.append("🏅 Wygrane: " + wins_text(won))
	if euro:
		lines.append(f"🇪🇺 Eurojackpot {_day(euro)}: {_fmt(sorted(euro['numbers']))} + {_fmt(sorted(euro['special']))}")
	if mini:
		lines.append(f"🍀 Mini Lotto {_day(mini)}: {_fmt(mini['numbers'])}")
	for i, (mine, games, total) in enumerate(my_results(lotto, won), 1):
		line = f"🎟️ Zestaw {i} ({_fmt(mine)}): " + ", ".join(
			f"{label} {len(hits)}/6" + (f" [{_fmt(hits)}]" if hits else "") + (f" = {_money(prize)}" if prize else "")
			for label, hits, prize in games
		)
		if total:
			line += f" 🎉 Mrrr, wygrałeś {_money(total)}! Część jest moja, na karmę."
		lines.append(line)
	lines.append(f"💰 Kumulacje: Lotto {_when(d['next_lotto'])} {_money(d['pot'])}" + (f" (Plus {_money(d['pot_plus'])})" if d["pot_plus"] else "")
		+ f" · Eurojackpot {_when(d['next_euro'])} {_money(d['euro_pot'])}")
	lines.append(f"🔥 Najczęściej ({draws} losowań, {HOT_COLD_MONTHS} mies.): {_fmt(hot)} · ❄️ najrzadziej: {_fmt(cold)}")
	spent = spending()
	if spent:
		lines.append(f"💸 Na Lotto w tym miesiącu {spent['this_month_zl']} zł (poprzedni: {spent['last_month_zl']} zł, średnio {spent['avg_per_month_zl']} zł)")
	return "\n".join(lines)


def tile():
	# Fields for the Homepage "Lotto" tile
	d = fetch()
	lotto, won = d["lotto"], d["won"]
	draw, plus = lotto["Lotto"], lotto.get("LottoPlus")
	sets = my_results(lotto, won)
	spent = spending()
	return {
		"last_draw": f"{_day(draw)}: {_fmt(draw['numbers'])}",
		"plus": _fmt(plus["numbers"]) if plus else "–",
		"prizes": wins_text(won) or "–",
		"my_sets": "; ".join(" ".join(f"{label} {len(hits)}/6" for label, hits, _ in games) for _, games, _ in sets) or "none saved (/lotto dodaj)",
		"my_won": _money(sum(total for *_, total in sets)),
		"next_lotto": f"{_when(d['next_lotto'])} · {_money(d['pot'])}" + (f" (Plus {_money(d['pot_plus'])})" if d["pot_plus"] else ""),
		"next_euro": f"{_when(d['next_euro'])} · {_money(d['euro_pot'])}",
		"spent_month": f"{spent['this_month_zl']} zł (last month {spent['last_month_zl']} zł)" if spent else "–",
		"updated": datetime.datetime.now(WARSAW).strftime("%d.%m %H:%M"),
	}
