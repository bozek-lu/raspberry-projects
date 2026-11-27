// Links follow the address the dashboard was opened with. services.yaml uses the Tailscale name;
// opened on the LAN (homepi.local or 192.168.x.x), links go to the same LAN host instead:
//   http://homepi.tail04e168.ts.net:PORT  ->  http://<this host>:PORT   (those ports listen on the LAN)
//   https://homepi.tail04e168.ts.net:8445 ->  http://<this host>:8096   (piano tutor, plain http at home)
// Actual (:443), Vaultwarden (:8443) and Paperless (:8444) stay on Tailscale: they only listen on the
// Pi itself and need HTTPS.
(() => {
	const TAILNET = "homepi.tail04e168.ts.net";
	const here = location.hostname;
	if (here === TAILNET || here.endsWith(".ts.net") || /^100\./.test(here)) return;
	const HTTPS_TO_LAN = { "8445": "8096" };

	function local(href) {
		let url;
		try { url = new URL(href); } catch { return href; }
		if (url.hostname !== TAILNET) return href;
		if (url.protocol === "http:") {
			url.hostname = here;
			return url.toString();
		}
		const lanPort = HTTPS_TO_LAN[url.port];
		if (lanPort) {
			url.protocol = "http:";
			url.hostname = here;
			url.port = lanPort;
			return url.toString();
		}
		return href;
	}

	function fix(root) {
		for (const a of root.querySelectorAll ? root.querySelectorAll(`a[href*="${TAILNET}"]`) : []) {
			const href = local(a.href);
			if (href !== a.href) a.href = href;
		}
	}

	// Homepage renders with React: fix links as they appear, and once more right before a click
	new MutationObserver(changes => {
		for (const change of changes) for (const node of change.addedNodes) if (node.nodeType === 1) fix(node);
	}).observe(document.documentElement, { childList: true, subtree: true });
	document.addEventListener("click", e => {
		const a = e.target.closest && e.target.closest("a[href]");
		if (a) a.href = local(a.href);
	}, true);
	fix(document);
})();
