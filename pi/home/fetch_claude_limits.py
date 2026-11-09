import datetime
import json
import os
import subprocess

# Fetches Claude plan limits on the Pi with CodexBar's CLI (every 15 minutes,
# claude-limits.timer), so they stay current while the Mac is off
CLI = os.path.expanduser("~/.local/bin/codexbar")
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_limits_pi.json")


def main():
	result = subprocess.run([CLI, "usage", "--provider", "claude", "--json"], capture_output=True, text=True, timeout=180)
	data = json.loads(result.stdout)[0]
	usage = data["usage"]
	pace = data.get("pace", {})
	summary = {
		"updated": datetime.datetime.now().strftime("%d.%m %H:%M"),
		"plan": usage.get("loginMethod"),
		"session_used": usage["primary"]["usedPercent"],
		"session_resets_at": usage["primary"]["resetsAt"],
		"week_used": usage["secondary"]["usedPercent"],
		"week_resets_at": usage["secondary"]["resetsAt"],
		"session_pace": pace.get("primary", {}).get("summary"),
		"week_pace": pace.get("secondary", {}).get("summary"),
	}
	with open(PATH + ".tmp", "w") as f:
		json.dump(summary, f)
	os.replace(PATH + ".tmp", PATH)


if __name__ == "__main__":
	main()
