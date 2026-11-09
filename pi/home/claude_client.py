import json
import os
import subprocess

import bot_stats

# Claude Code in headless mode, signed in with the subscription token
# (CLAUDE_CODE_OAUTH_TOKEN in .env). Runs in an empty folder so it has
# nothing on the Pi to read, and gets no tools at all.
CLAUDE = os.path.expanduser("~/.local/bin/claude")
WORKDIR = os.path.expanduser("~/.pandora-claude")


def ask_claude(prompt, system=None):
	os.makedirs(WORKDIR, exist_ok=True)
	cmd = [CLAUDE, "-p", prompt, "--tools", "", "--output-format", "json"]
	if system:
		cmd += ["--append-system-prompt", system]
	try:
		result = subprocess.run(cmd, cwd=WORKDIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
	except Exception as e:
		print(f"Claude error: {e}")
		return None
	try:
		data = json.loads(result.stdout)
	except ValueError:
		print(f"Claude error: {(result.stderr or result.stdout).strip()[:300]}")
		return None
	if data.get("is_error"):
		print(f"Claude error: {str(data.get('result'))[:300]}")
		return None
	usage = data.get("usage", {})
	tokens = sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
	bot_stats.record_claude(tokens, data.get("total_cost_usd", 0))
	return (data.get("result") or "").strip() or None
