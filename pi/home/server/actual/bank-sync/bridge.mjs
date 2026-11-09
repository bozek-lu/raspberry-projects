// Small local API over Actual Budget for the Telegram bot's daily spending review.
// Keeps the budget open so button presses answer quickly. Listens on 127.0.0.1 only.
import { createServer } from "node:http";
import * as api from "@actual-app/api";

const { ACTUAL_PASSWORD, ACTUAL_SYNC_ID } = process.env;
const ACCOUNT = "mBank Personal";
const OWN_TRANSFERS = "Between my accounts";
const PORT = 5007;

await api.init({ dataDir: "/app/cache-bridge", serverURL: "http://127.0.0.1:5006", password: ACTUAL_PASSWORD });
await api.downloadBudget(ACTUAL_SYNC_ID);

const ymd = d => d.toLocaleDateString("sv-SE");

async function categories() {
	const list = [];
	for (const g of await api.getCategoryGroups())
		if (!g.is_income) for (const c of g.categories) if (!c.hidden && c.name !== OWN_TRANSFERS) list.push({ id: c.id, name: c.name, group: g.name });
	return list;
}

async function review(days) {
	const cats = Object.fromEntries((await api.getCategories()).map(c => [c.id, c.name]));
	const payees = Object.fromEntries((await api.getPayees()).map(p => [p.id, p.name]));
	const account = (await api.getAccounts()).find(a => a.name === ACCOUNT);
	const start = ymd(new Date(Date.now() - days * 86400000));
	const tx = (await api.getTransactions(account.id, start, ymd(new Date())))
		.filter(t => t.amount < 0 && !t.is_parent && cats[t.category] !== OWN_TRANSFERS)
		.map(t => ({
			id: t.id, date: t.date, payee: payees[t.payee] ?? t.imported_payee ?? "?", payee_id: t.payee,
			amount_zl: -t.amount / 100, category_id: t.category ?? null, category: cats[t.category] ?? null, notes: t.notes ?? "",
		}));
	return { categories: await categories(), transactions: tx };
}

async function categorize({ id, category_id, payee_id, remember }) {
	await api.updateTransaction(id, { category: category_id });
	if (remember && payee_id) {
		const existing = (await api.getRules()).find(r =>
			r.conditions.length === 1 && r.conditions[0].field === "payee" && r.conditions[0].op === "is" && r.conditions[0].value === payee_id);
		const actions = [{ op: "set", field: "category", value: category_id }];
		if (existing) await api.updateRule({ ...existing, actions });
		else await api.createRule({ stage: null, conditionsOp: "and", conditions: [{ field: "payee", op: "is", value: payee_id }], actions });
	}
	await api.sync();
	return { ok: true };
}

createServer(async (req, res) => {
	try {
		await api.sync();
		const url = new URL(req.url, "http://localhost");
		let body = "";
		for await (const chunk of req) body += chunk;
		let result;
		if (req.method === "GET" && url.pathname === "/review") result = await review(Number(url.searchParams.get("days") ?? 7));
		else if (req.method === "POST" && url.pathname === "/categorize") result = await categorize(JSON.parse(body));
		else { res.writeHead(404).end(); return; }
		res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(result));
	} catch (e) {
		console.error(e);
		res.writeHead(500, { "content-type": "application/json" }).end(JSON.stringify({ error: String(e?.message ?? e) }));
	}
}).listen(PORT, "127.0.0.1", () => console.log(`bridge listening on ${PORT}`));
