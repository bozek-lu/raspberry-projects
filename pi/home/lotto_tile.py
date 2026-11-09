import json
import os

from dotenv import load_dotenv

import lotto

# Writes the Homepage "Lotto" tile data (run hourly by cron)
OUT = "/home/lbx/server/homepage/public/lotto.json"

load_dotenv("/home/lbx/.env")
data = lotto.tile()
with open(OUT + ".tmp", "w") as f:
	json.dump(data, f, ensure_ascii=False)
os.replace(OUT + ".tmp", OUT)
