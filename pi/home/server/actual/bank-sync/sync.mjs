// Pulls new bank transactions into Actual (run daily by cron via run.sh).
// Pings Uptime Kuma after each run; Kuma alerts on Telegram if a run fails or none happens for 25 hours.
import * as api from "@actual-app/api";

const { ACTUAL_PASSWORD, ACTUAL_SYNC_ID, KUMA_PUSH_URL } = process.env;

const ping = (status, msg) =>
	fetch(`${KUMA_PUSH_URL}?status=${status}&msg=${encodeURIComponent(msg.slice(0, 200))}`).catch(() => {});

try {
	await api.init({ dataDir: "/app/cache", serverURL: "http://127.0.0.1:5006", password: ACTUAL_PASSWORD });
	await api.downloadBudget(ACTUAL_SYNC_ID);
	await api.runBankSync();
	await api.shutdown();
	await ping("up", "Synced");
	console.log(new Date().toISOString(), "synced");
} catch (e) {
	await ping("down", String(e?.message ?? e));
	throw e;
}
