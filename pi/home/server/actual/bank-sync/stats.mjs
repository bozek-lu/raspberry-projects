// Writes personal-account budget numbers from Actual for the Homepage "Budget" tiles and the morning message
// (run hourly by cron and after each bank sync). The business account is left out on purpose.
import { renameSync, writeFileSync } from "node:fs";
import * as api from "@actual-app/api";

const { ACTUAL_PASSWORD, ACTUAL_SYNC_ID } = process.env;
const OUT = "/homepage-public/finance.json";
// Per-category detail for the morning savings tip; kept out of Homepage's public folder
const SPENDING_OUT = "/app/spending.json";
const ACCOUNT = "mBank Personal";
const OWN_TRANSFERS = "Between my accounts";
// AI tools are never suggested as savings, so they are left out of the savings-tip data
const AI_PAYEES = /ANTHROPIC|CLAUDE|T3 CHAT|OPENAI|CHATGPT|CURSOR|PERPLEXITY|MIDJOURNEY|GEMINI|COPILOT|GITHUB|MISTRAL/i;

const pln = grosze => new Intl.NumberFormat("pl-PL", { style: "currency", currency: "PLN", maximumFractionDigits: 0 }).format(grosze / 100);
const ymd = d => d.toLocaleDateString("sv-SE");

const now = new Date();
const monthStart = ymd(new Date(now.getFullYear(), now.getMonth(), 1));
const lastMonthStart = ymd(new Date(now.getFullYear(), now.getMonth() - 1, 1));
const threeMonthsStart = ymd(new Date(now.getFullYear(), now.getMonth() - 3, 1));

await api.init({ dataDir: "/app/cache", serverURL: "http://127.0.0.1:5006", password: ACTUAL_PASSWORD });
await api.downloadBudget(ACTUAL_SYNC_ID);

const categories = Object.fromEntries((await api.getCategories()).map(c => [c.id, c.name]));
const payees = Object.fromEntries((await api.getPayees()).map(p => [p.id, p.name]));
const account = (await api.getAccounts()).find(a => a.name === ACCOUNT);
const tx = (await api.getTransactions(account.id, threeMonthsStart, ymd(now)))
	.filter(t => !t.is_parent && !t.starting_balance_flag)
	.map(t => ({ ...t, cat: categories[t.category] ?? "Uncategorized" }));
const balance = await api.getAccountBalance(account.id);
await api.shutdown();

const since = (start, end = "9999") => tx.filter(t => t.date >= start && t.date < end);
// Money from the business account counts as income here; money sent back to it is not spending
const earned = list => list.filter(t => t.amount > 0).reduce((s, t) => s + t.amount, 0);
const spent = list => -list.filter(t => t.amount < 0 && t.cat !== OWN_TRANSFERS).reduce((s, t) => s + t.amount, 0);
const topCategories = (list, n) => {
	const by = {};
	for (const t of list) if (t.amount < 0 && t.cat !== OWN_TRANSFERS) by[t.cat] = (by[t.cat] ?? 0) - t.amount;
	return Object.entries(by).sort((a, b) => b[1] - a[1]).slice(0, n).map(([c, v]) => `${c} ${pln(v)}`).join(", ") || "–";
};

const thisMonth = since(monthStart);
const stats = {
	updated: now.toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }),
	balance: pln(balance),
	month_in: pln(earned(thisMonth)),
	month_out: pln(spent(thisMonth)),
	month_saved: pln(earned(thisMonth) - spent(thisMonth)),
	last_month_out: pln(spent(since(lastMonthStart, monthStart))),
	avg_out_3m: pln(spent(since(threeMonthsStart, monthStart)) / 3),
	top_month: topCategories(thisMonth, 3),
	uncategorized: String(tx.filter(t => !t.category).length),
};

const writeJson = (path, data) => {
	writeFileSync(path + ".tmp", JSON.stringify(data));
	renameSync(path + ".tmp", path);
};
writeJson(OUT, stats);

const zl = grosze => Math.round(grosze / 100);
const byCategory = {};
for (const t of tx) {
	if (t.amount >= 0 || t.cat === OWN_TRANSFERS) continue;
	const name = payees[t.payee] ?? t.imported_payee ?? "?";
	if (AI_PAYEES.test(name)) continue;
	const c = (byCategory[t.cat] ??= { this_month: 0, last_month: 0, prev_3_months: 0, payees: {} });
	if (t.date >= monthStart) c.this_month -= t.amount;
	else {
		c.prev_3_months -= t.amount;
		if (t.date >= lastMonthStart) c.last_month -= t.amount;
	}
	c.payees[name] = (c.payees[name] ?? 0) - t.amount;
}
const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
writeJson(SPENDING_OUT, {
	day_of_month: now.getDate(),
	days_in_month: daysInMonth,
	categories: Object.entries(byCategory)
		.map(([category, c]) => ({
			category,
			this_month_zl: zl(c.this_month),
			last_month_zl: zl(c.last_month),
			avg_per_month_zl: zl(c.prev_3_months / 3),
			top_payees_4_months_zl: Object.fromEntries(Object.entries(c.payees).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([n, v]) => [n, zl(v)])),
		}))
		.sort((a, b) => b.avg_per_month_zl - a.avg_per_month_zl),
});
console.log(new Date().toISOString(), "stats written");
