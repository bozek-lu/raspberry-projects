// One-off: categorizes past transactions, adds rules for future bank syncs,
// and builds a report dashboard per account. Dry run unless APPLY=1.
import * as api from "@actual-app/api";

const { ACTUAL_PASSWORD, ACTUAL_SYNC_ID, APPLY } = process.env;

const ACCOUNT_NAMES = { "REDACTED": "mBank Personal", "REDACTED": "mBank Business (FDD)" };

// [group, category, payee/description patterns (case-insensitive substrings)]
const CATEGORIES = [
	["Everyday", "Groceries", ["BIEDRONKA", "LIDL", "KAUFLAND", "SPOLEM", "SWOJAK", "GROSZEK", "ZABKA", "JUTRZENKA"]],
	["Everyday", "Eating out & delivery", ["PYSZNE.PL", "GLOVO", "UBER * EATS", "UBER *EATS", "JUST EAT", "HALO-DOSTAWA", "DIETLY", "KEBAB", "SUBWAY", "MCDONALDS", "PL BK WARSZAWA", "BK KRAKOW", "GREEN COFFEE", "LIQUID JUNGLE", "RELAY"]],
	["Everyday", "Transport", ["BOLT.EU", "UBER *TRIP", "KOLEO", "PLK "]],
	["Everyday", "Pets", ["ZOOPLUS", "WETERYNARYJNY"]],
	["Everyday", "Health & insurance", ["UNIQA", "GENERALI", "UBEZP. NA WYPADEK"]],
	["Home", "Housing fees", ["REDACTED"]],
	["Home", "Energy, internet & phone", ["GKPGE", "T-MOBILE", "DERKOM"]],
	["Home", "Home & repairs", ["CASTORAMA", "BRICOMARCHE", "OSLONAOKNA", "REDACTED"]],
	["Lifestyle", "Shopping", ["ALLEGRO", "AMAZON.PL", "X-KOM", "MEDIAEXPERT", "TEMU", "ZALANDO", "DECATHLON", "MILITARIA", "PAYU S.A.", "WAPER", "INPOST", "POCZTA POLSKA", "PUBLIO", "IMPAR", "MPL SERVICES"]],
	["Lifestyle", "Subscriptions", ["NETFLIX", "APPLE.COM/BILL", "ANTHROPIC", "T3 CHAT", "MUBI", "AMAZON PRIME"]],
	["Lifestyle", "Games & fun", ["WARGAMING", "STEAMPOWERED", "GAMEFORGE", "CRAZYTIME"]],
	["Lifestyle", "Lottery", ["LOTTO"]],
	["Lifestyle", "Travel", ["RYANAIR", "BOOKING", "AIRPORT HOTEL", "FLIXBUS", "BALTONA"]],
	["Lifestyle", "Gifts & charity", ["UNICEF", "POCZTAKWIATOWA"]],
	["People", "Family & friends", ["REDACTED", "REDACTED", "REDACTED", "REDACTED", "REDACTED", "REDACTED", "REDACTED", "REDACTED", "MARCIN", "MAMA", "REDACTED"]],
	["Business & taxes", "ZUS", ["ZAKŁAD UBEZPIECZEŃ SPOŁECZNYCH"]],
	["Business & taxes", "Taxes", ["URZĄD SKARBOWY", "RATUSZ"]],
	["Business & taxes", "Accountant", ["KSIEGOWNIK"]],
	["Business & taxes", "Bank fees", ["OPŁATA PRZELEW", "BLIK PROW."]],
	["Money moves", "Cash withdrawals", ["WYPŁATA ATM"]],
	["Money moves", "Revolut", ["REVOLUT"]],
	// Last, so payments that only mention my name in the notes (e.g. housing fees) match first
	["Money moves", "Between my accounts", ["REDACTED"]],
	["Income", "Business income", ["UZNANIE NATYCH. TRANSAKCJA WALUT"]],
	["Income", "Brokerage withdrawals", [" W BM "]],
];

// Card payments get a new payee per order number; merge them into one payee
const PAYEE_GROUPS = {
	"Pyszne.pl": "PYSZNE.PL", "Glovo": "GLOVO", "Bolt": "BOLT.EU", "Amazon.pl": "AMAZON.PL*", "Biedronka": "BIEDRONKA",
	"Allegro": "ALLEGRO", "Lotto": "LOTTO", "Zooplus": "ZOOPLUS", "Uber Eats": "EATS", "Ryanair": "RYANAIR", "Kaufland": "KAUFLAND",
};

const DEFAULT_CATEGORIES = ["Food", "General", "Bills", "Bills (Flexible)"];

const apply = APPLY === "1";
const lib = await api.init({ dataDir: "/app/cache", serverURL: "http://127.0.0.1:5006", password: ACTUAL_PASSWORD });
await api.downloadBudget(ACTUAL_SYNC_ID);

const accounts = await api.getAccounts();
let payees = await api.getPayees();
const payeeById = () => Object.fromEntries(payees.map(p => [p.id, p]));
const match = (text, patterns) => patterns.some(p => text.toUpperCase().includes(p));
const categoryFor = text => CATEGORIES.find(([, , pats]) => match(text, pats));

// 1. Accounts
for (const a of accounts)
	if (ACCOUNT_NAMES[a.name]) {
		console.log(`account: ${a.name} -> ${ACCOUNT_NAMES[a.name]}`);
		if (apply) await api.updateAccount(a.id, { name: ACCOUNT_NAMES[a.name] });
	}

// 2. Categories
const groups = await api.getCategoryGroups();
const catId = {};
for (const g of groups) for (const c of g.categories) catId[c.name] = c.id;
for (const [groupName, name] of CATEGORIES) {
	if (catId[name]) continue;
	let g = groups.find(x => x.name === groupName);
	if (!g && apply) {
		g = { name: groupName, id: await api.createCategoryGroup({ name: groupName, is_income: groupName === "Income" }), categories: [] };
		groups.push(g);
	}
	catId[name] = apply ? await api.createCategory({ name, group_id: g.id }) : `new:${name}`;
}

// 3. Merge payee variants
for (const [clean, pat] of Object.entries(PAYEE_GROUPS)) {
	const members = payees.filter(p => !p.transfer_acct && p.name.toUpperCase().includes(pat));
	if (members.length === 0) continue;
	console.log(`payee: ${members.length} -> ${clean}`);
	if (!apply) continue;
	const [target, ...rest] = members;
	if (rest.length) await api.mergePayees(target.id, rest.map(p => p.id));
	await api.updatePayee(target.id, { name: clean });
}
payees = await api.getPayees();
const byId = payeeById();

// 4. Categorize history
const counts = {}, unmatched = {};
const updates = [];
for (const a of accounts) {
	for (const t of await api.getTransactions(a.id, "2000-01-01", "2100-01-01")) {
		if (t.is_parent || t.starting_balance_flag) continue;
		const payee = byId[t.payee];
		if (payee?.transfer_acct) {
			counts["(own transfer, no category)"] = (counts["(own transfer, no category)"] || 0) + 1;
			if (t.category) updates.push([t.id, { category: null }]);
			continue;
		}
		const text = [payee?.name, t.imported_payee, t.notes].filter(Boolean).join(" ");
		const hit = categoryFor(text);
		if (!hit) {
			const k = payee?.name ?? "(no payee)";
			unmatched[k] = (unmatched[k] || 0) + 1;
			continue;
		}
		counts[hit[1]] = (counts[hit[1]] || 0) + 1;
		if (t.category !== catId[hit[1]]) updates.push([t.id, { category: catId[hit[1]] }]);
	}
}
console.log("per category:", JSON.stringify(counts));
console.log("unmatched:", JSON.stringify(unmatched));
console.log("updates:", updates.length);
if (apply) await api.batchBudgetUpdates(async () => { for (const [id, f] of updates) await api.updateTransaction(id, f); });

// 5. Rules for future syncs
if (apply && (await api.getRules()).length === 0) {
	const cleanPayeeId = name => payees.find(p => p.name === name)?.id;
	for (const [, name, pats] of CATEGORIES)
		for (const pat of pats) {
			const field = pat === "WYPŁATA ATM" ? "notes" : "imported_payee";
			const actions = [{ op: "set", field: "category", value: catId[name] }];
			const clean = Object.entries(PAYEE_GROUPS).find(([, p]) => pat.includes(p) || p.includes(pat.replace("*", "")))?.[0];
			if (clean && cleanPayeeId(clean)) actions.push({ op: "set", field: "payee", value: cleanPayeeId(clean) });
			await api.createRule({ stage: null, conditionsOp: "and", conditions: [{ field, op: "contains", value: pat.trim() }], actions });
		}
	console.log("rules:", (await api.getRules()).length);
}

// 6. Remove the unused starter categories
if (apply) {
	const used = new Set();
	for (const a of accounts) for (const t of await api.getTransactions(a.id, "2000-01-01", "2100-01-01")) used.add(t.category);
	for (const g of await api.getCategoryGroups()) {
		for (const c of g.categories) if (DEFAULT_CATEGORIES.includes(c.name) && !used.has(c.id)) await api.deleteCategory(c.id);
		const left = (await api.getCategoryGroups()).find(x => x.id === g.id);
		if (left && left.categories.length === 0) await api.deleteCategoryGroup(g.id);
	}
}

// 7. One report dashboard per account
if (apply) {
	const { data: pages } = await lib.send("api/query", { query: { table: "dashboard_pages", filterExpressions: [{ tombstone: false }], selectExpressions: ["*"], groupExpressions: [], orderExpressions: [], limit: null, offset: null, calculation: false, rawMode: false, withDead: false, validateRefs: true, tableOptions: {} } }).catch(() => ({ data: [] }));
	for (const a of accounts) {
		const label = ACCOUNT_NAMES[a.name] ?? a.name;
		if (pages.some(p => p.name === label)) continue;
		const pageId = await lib.send("dashboard-create", { name: label });
		const acct = [{ field: "account", op: "is", value: a.id }, { field: "transfer", op: "is", value: false }];
		const year = { start: "2025-11-01", end: "2026-10-31", mode: "sliding-window" };
		const summary = (name, op, type, x) => ({
			type: "summary-card", width: 4, height: 2, x, y: 0,
			meta: { name, content: JSON.stringify({ type, fontSize: 20 }), timeFrame: year, conditionsOp: "and", conditions: [{ field: "amount", op, value: 0 }, ...acct] },
		});
		const widgets = [
			summary("Money in (12 months)", "gt", "sum", 0),
			summary("Money out (12 months)", "lt", "sum", 4),
			summary("Spent per month", "lt", "avgPerMonth", 8),
			{ type: "net-worth-card", width: 6, height: 2, x: 0, y: 2, meta: { name: "Balance", conditionsOp: "and", conditions: [{ field: "account", op: "is", value: a.id }] } },
			{ type: "cash-flow-card", width: 6, height: 2, x: 6, y: 2, meta: { name: "Cash flow", conditionsOp: "and", conditions: acct } },
		];
		const report = (name, extra) => lib.send("report/create", {
			name: `${name} – ${label}`, startDate: "2025-11-01", endDate: "2026-10-31", isDateStatic: false, dateRange: "Last 12 months",
			groupBy: "Category", interval: "Monthly", balanceType: "Payment", showEmpty: false, showOffBudget: false, showHiddenCategories: false,
			showUncategorized: true, trimIntervals: true, showTrendLines: false, includeCurrentInterval: true, conditions: acct, conditionsOp: "and", ...extra,
		});
		widgets.push({ type: "custom-report", width: 6, height: 3, x: 0, y: 4, meta: { id: await report("Spending by category", { mode: "total", graphType: "DonutGraph" }) } });
		widgets.push({ type: "custom-report", width: 6, height: 3, x: 6, y: 4, meta: { id: await report("Monthly spending", { mode: "time", graphType: "StackedBarGraph" }) } });
		for (const w of widgets) await lib.send("dashboard-add-widget", { ...w, dashboard_page_id: pageId });
		console.log(`dashboard: ${label} (${widgets.length} widgets)`);
	}
}

await api.shutdown();
