/**
 * Phantom / Solflare browser-extension connect helpers.
 * Extension connect verifies the wallet in the browser; Live bot auto-sign
 * still requires Set Wallet (base58) so the Flask server can sign swaps.
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "solDexConnectedWallet";

  function shortPubkey(pk) {
    if (!pk || typeof pk !== "string") return "—";
    if (pk.length <= 12) return pk;
    return pk.slice(0, 4) + "…" + pk.slice(-4);
  }

  function detectProviders() {
    const phantom =
      (global.window && global.window.phantom && global.window.phantom.solana) ||
      (global.window &&
        global.window.solana &&
        global.window.solana.isPhantom &&
        global.window.solana) ||
      null;
    const solflare =
      (global.window && global.window.solflare) ||
      (global.window &&
        global.window.solana &&
        global.window.solana.isSolflare &&
        global.window.solana) ||
      null;
    return { phantom, solflare };
  }

  function loadStored() {
    try {
      const raw = global.localStorage && global.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (parsed && parsed.pubkey && parsed.provider) return parsed;
    } catch (_) {}
    return null;
  }

  function saveStored(provider, pubkey) {
    try {
      if (global.localStorage) {
        global.localStorage.setItem(
          STORAGE_KEY,
          JSON.stringify({ provider, pubkey, at: Date.now() })
        );
      }
    } catch (_) {}
  }

  function clearStored() {
    try {
      if (global.localStorage) global.localStorage.removeItem(STORAGE_KEY);
    } catch (_) {}
  }

  async function connectProvider(name) {
    const { phantom, solflare } = detectProviders();
    let provider = null;
    if (name === "phantom") provider = phantom;
    else if (name === "solflare") provider = solflare;
    if (!provider) {
      const err = new Error(
        name === "phantom"
          ? "Phantom extension not found. Install Phantom, then refresh."
          : "Solflare extension not found. Install Solflare, then refresh."
      );
      err.code = "provider_missing";
      throw err;
    }
    const resp = await provider.connect();
    const pk =
      (resp && resp.publicKey && resp.publicKey.toString && resp.publicKey.toString()) ||
      (provider.publicKey && provider.publicKey.toString && provider.publicKey.toString()) ||
      null;
    if (!pk) {
      const err = new Error("Wallet connected but no public key returned");
      err.code = "no_pubkey";
      throw err;
    }
    saveStored(name, pk);
    return { provider: name, pubkey: pk, adapter: provider };
  }

  async function disconnectProvider(name) {
    const { phantom, solflare } = detectProviders();
    const provider = name === "solflare" ? solflare : phantom;
    try {
      if (provider && typeof provider.disconnect === "function") {
        await provider.disconnect();
      }
    } catch (_) {}
    clearStored();
  }

  global.SolDexWalletConnect = {
    STORAGE_KEY,
    shortPubkey,
    detectProviders,
    loadStored,
    saveStored,
    clearStored,
    connectProvider,
    disconnectProvider,
  };
})(typeof window !== "undefined" ? window : globalThis);
