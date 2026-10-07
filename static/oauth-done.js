// Runs in the sign-in popup: tells the main page how sign-in went, then closes.
// Google's pages cut the link to window.opener, so use a same-origin BroadcastChannel instead.
const result = JSON.parse(document.body.dataset.result);
new BroadcastChannel('google-auth').postMessage(result);
if (result.ok) window.close();
