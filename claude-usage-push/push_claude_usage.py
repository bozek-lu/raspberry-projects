#!/usr/bin/env python3
# Sends Claude plan usage from CodexBar on this Mac to the homepi dashboard.
# Run every 15 minutes by launchd (see com.homepi.claude-usage.plist).
import datetime
import json
import subprocess

CLI = "/Applications/CodexBar.app/Contents/Helpers/CodexBarCLI"
PI = "lbx@homepi.tail04e168.ts.net"


def codexbar(command):
	result = subprocess.run([CLI, command, "--provider", "claude", "--json"], capture_output=True, text=True, timeout=180)
	return json.loads(result.stdout)[0]


def main():
	usage = codexbar("usage")
	cost = codexbar("cost")
	session, week = usage["usage"]["primary"], usage["usage"]["secondary"]
	pace = usage.get("pace", {})
	today = next((d for d in cost.get("daily", []) if d["date"] == datetime.date.today().isoformat()), {})
	summary = {
		"updated": datetime.datetime.now().strftime("%d.%m %H:%M"),
		"plan": usage["usage"].get("loginMethod"),
		"session_used": session["usedPercent"],
		"session_resets_at": session["resetsAt"],
		"week_used": week["usedPercent"],
		"week_resets_at": week["resetsAt"],
		"session_pace": pace.get("primary", {}).get("summary"),
		"week_pace": pace.get("secondary", {}).get("summary"),
		"today_cost": today.get("totalCost", 0),
		"today_tokens": today.get("totalTokens", 0),
		"cost_30d": cost.get("last30DaysCostUSD", 0),
		"tokens_30d": cost.get("last30DaysTokens", 0),
		"top_model": (today.get("modelsUsed") or [None])[0],
	}
	subprocess.run(
		["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", PI,
		 "cat > ~/claude_usage.json.tmp && mv ~/claude_usage.json.tmp ~/claude_usage.json"],
		input=json.dumps(summary), text=True, timeout=60, check=True,
	)


if __name__ == "__main__":
	main()
