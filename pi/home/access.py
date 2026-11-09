import json
import os

# Who may talk to Pandora, kept next to the bot so it survives restarts
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandora_access.json")


def load():
	try:
		with open(PATH) as f:
			return json.load(f)
	except FileNotFoundError:
		return {"allowed": {}, "pending": {}, "rejected": {}}


def save(data):
	with open(PATH + ".tmp", "w") as f:
		json.dump(data, f, indent=1, ensure_ascii=False)
	os.replace(PATH + ".tmp", PATH)


def state(user_id):
	data = load()
	for key in ("allowed", "pending", "rejected"):
		if str(user_id) in data[key]:
			return key
	return None


def set_state(user_id, name, new_state):
	data = load()
	for key in ("allowed", "pending", "rejected"):
		data[key].pop(str(user_id), None)
	if new_state:
		data[new_state][str(user_id)] = name
	save(data)


def name_of(user_id):
	data = load()
	for key in ("allowed", "pending", "rejected"):
		if str(user_id) in data[key]:
			return data[key][str(user_id)]
	return str(user_id)
