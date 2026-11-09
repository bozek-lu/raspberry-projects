import datetime
import json
import os
import time
import urllib.request

# Zamość; change to move the forecast
LATITUDE, LONGITUDE = 50.72, 23.25
PLACE = "Zamościu"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_cache.json")
MAX_AGE = 1800

SKY = {
	0: "bezchmurnie ☀️", 1: "prawie bezchmurnie 🌤", 2: "częściowe zachmurzenie ⛅", 3: "pochmurno ☁️",
	45: "mgła 🌫", 48: "mgła 🌫", 51: "mżawka 🌦", 53: "mżawka 🌦", 55: "mżawka 🌦",
	61: "deszcz 🌧", 63: "deszcz 🌧", 65: "ulewa 🌧", 71: "śnieg 🌨", 73: "śnieg 🌨", 75: "śnieżyca ❄️",
	80: "przelotny deszcz 🌦", 81: "przelotny deszcz 🌦", 82: "ulewa 🌧", 95: "burza ⛈",
}


def fetch():
	url = (
		"https://api.open-meteo.com/v1/forecast"
		f"?latitude={LATITUDE}&longitude={LONGITUDE}&timezone=Europe%2FWarsaw&forecast_days=1"
		"&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
		"precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,uv_index_max,sunrise,sunset"
		"&hourly=temperature_2m,precipitation_probability"
	)
	with urllib.request.urlopen(url, timeout=10) as r:
		return json.load(r)


def forecast():
	# Cached so the dashboard feed (every minute) does not hit the weather service each time
	try:
		with open(CACHE) as f:
			cached = json.load(f)
		if time.time() - cached["at"] < MAX_AGE and cached["date"] == datetime.date.today().isoformat():
			return cached["data"]
	except (FileNotFoundError, ValueError, KeyError):
		pass
	try:
		data = fetch()
	except Exception:
		return None
	with open(CACHE + ".tmp", "w") as f:
		json.dump({"at": time.time(), "date": datetime.date.today().isoformat(), "data": data}, f)
	os.replace(CACHE + ".tmp", CACHE)
	return data


def rain_window(hourly):
	# First to last hour today with at least 50% chance of rain
	hours = [t[11:16] for t, p in zip(hourly["time"], hourly["precipitation_probability"]) if p is not None and p >= 50]
	if not hours:
		return None
	end = (datetime.datetime.strptime(hours[-1], "%H:%M") + datetime.timedelta(hours=1)).strftime("%H:%M")
	return f"{hours[0]}–{end}"


def today():
	data = forecast()
	if not data:
		return None
	d = {k: v[0] for k, v in data["daily"].items()}
	temps = dict(zip((t[11:13] for t in data["hourly"]["time"]), data["hourly"]["temperature_2m"]))
	return {
		"sky": SKY.get(d["weather_code"], "zmiennie"),
		"min": round(d["temperature_2m_min"]),
		"max": round(d["temperature_2m_max"]),
		"morning": round(temps.get("08", d["temperature_2m_min"])),
		"afternoon": round(temps.get("14", d["temperature_2m_max"])),
		"evening": round(temps.get("20", d["temperature_2m_min"])),
		"rain_chance": d["precipitation_probability_max"] or 0,
		"rain_mm": d["precipitation_sum"] or 0,
		"rain_hours": rain_window(data["hourly"]),
		"wind": round(d["wind_speed_10m_max"]),
		"gusts": round(d["wind_gusts_10m_max"]),
		"uv": round(d["uv_index_max"] or 0),
		"sunrise": d["sunrise"][11:16],
		"sunset": d["sunset"][11:16],
	}


def message():
	w = today()
	if not w:
		return "🌤 Pogoda: brak danych"
	lines = [
		f"🌤 Pogoda w {PLACE}: {w['sky']}",
		f"🌡 {w['min']}–{w['max']} °C (rano {w['morning']}°, po południu {w['afternoon']}°, wieczorem {w['evening']}°)",
		f"🌧 Deszcz: {w['rain_chance']}% szans, {w['rain_mm']:.1f} mm"
		+ (f", najpewniej {w['rain_hours']}" if w["rain_hours"] else ""),
		f"💨 Wiatr do {w['wind']} km/h, porywy do {w['gusts']} km/h",
		f"🌅 Wschód {w['sunrise']}, zachód {w['sunset']}, UV {w['uv']}",
	]
	# Chance alone can be high with almost no rain expected, so require some amount too
	if w["rain_chance"] >= 50 and w["rain_mm"] >= 0.5:
		lines.append("☂️ Weź parasol!")
	return "\n".join(lines)


def dashboard():
	w = today()
	if not w:
		return {"weather_sky": "brak danych"}
	return {
		"weather_sky": w["sky"],
		"weather_temp": f"{w['min']}–{w['max']} °C (14:00: {w['afternoon']}°)",
		"weather_rain": f"{w['rain_chance']}%, {w['rain_mm']:.1f} mm",
		"weather_rain_hours": w["rain_hours"] or "–",
		"weather_wind": f"{w['wind']} km/h, gusts {w['gusts']}",
		"weather_sun": f"{w['sunrise']} – {w['sunset']}, UV {w['uv']}",
	}


if __name__ == "__main__":
	print(message())
