/**
 * Phantom / Solflare browser-extension connect helpers.
 * Extension connect verifies the wallet in the browser; Live bot auto-sign
 * still requires Set Wallet (base58) so the Flask server can sign swaps.
 *
 * Critical: user-initiated Connect MUST call provider.connect({ onlyIfTrusted: false })
 * so the extension shows the approval popup. never use onlyIfTrusted:true on that path.
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "solDexConnectedWallet";
  const PHANTOM_INSTALL = "https://phantom.app/download";
  const SOLFLARE_INSTALL = "https://solflare.com/download";
  const PROVIDER_WAIT_MS = 2000;
  const PROVIDER_POLL_MS = 100;

  function win() {
    if (typeof global.window !== "undefined" && global.window) return global.window;
    return global;
  }

  function shortPubkey(pk) {
    if (!pk || typeof pk !== "string") return "—";
    if (pk.length <= 12) return pk;
    return pk.slice(0, 4) + "…" + pk.slice(-4);
  }

  function getPhantomProvider() {
    const w = win();
    try {
      if (w.phantom && w.phantom.solana) return w.phantom.solana;
      if (w.solana && w.solana.isPhantom) return w.solana;
    } catch (_) {}
    return null;
  }

  function getSolflareProvider() {
    const w = win();
    try {
      if (w.solflare) return w.solflare;
      if (w.solana && w.solana.isSolflare) return w.solana;
    } catch (_) {}
    return null;
  }

  function detectProviders() {
    return { phantom: getPhantomProvider(), solflare: getSolflareProvider() };
  }

  function providerMissingMessage(name) {
    if (name === "phantom") {
      return "Phantom extension not found. Install Phantom (" + PHANTOM_INSTALL + "), then refresh.";
    }
    return "Solflare extension not found. Install Solflare (" + SOLFLARE_INSTALL + "), then refresh.";
  }

  /**
   * Wait for late injection (common on localhost / Brave). Resolves with
   * whatever is present when timeout elapses — callers still check for null.
   */
  function waitForProviders(timeoutMs) {
    const ms = typeof timeoutMs === "number" ? timeoutMs : PROVIDER_WAIT_MS;
    return new Promise(function (resolve) {
      const start = Date.now();
      const w = win();

      function done() {
        cleanup();
        resolve(detectProviders());
      }

      function check() {
        const p = detectProviders();
        if (p.phantom || p.solflare) {
          done();
          return true;
        }
        if (Date.now() - start >= ms) {
          done();
          return true;
        }
        return false;
      }

      function onReady() {
        check();
      }

      function cleanup() {
        try {
          if (w.removeEventListener) {
            w.removeEventListener("solana#initialized", onReady);
            w.removeEventListener("phantom#initialized", onReady);
          }
        } catch (_) {}
        if (timer) clearInterval(timer);
      }

      if (check()) return;

      try {
        if (w.addEventListener) {
          w.addEventListener("solana#initialized", onReady);
          w.addEventListener("phantom#initialized", onReady);
        }
      } catch (_) {}

      var timer = setInterval(function () {
        check();
      }, PROVIDER_POLL_MS);
    });
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
          JSON.stringify({ provider: provider, pubkey: pubkey, at: Date.now() })
        );
      }
    } catch (_) {}
  }

  function clearStored() {
    try {
      if (global.localStorage) global.localStorage.removeItem(STORAGE_KEY);
    } catch (_) {}
  }

  function extractPubkey(resp, provider) {
    if (resp && resp.publicKey) {
      if (typeof resp.publicKey.toString === "function") return resp.publicKey.toString();
      if (typeof resp.publicKey.toBase58 === "function") return resp.publicKey.toBase58();
      if (typeof resp.publicKey === "string") return resp.publicKey;
    }
    if (provider && provider.publicKey) {
      if (typeof provider.publicKey.toString === "function") return provider.publicKey.toString();
      if (typeof provider.publicKey.toBase58 === "function") return provider.publicKey.toBase58();
    }
    if (resp && typeof resp.address === "string") return resp.address;
    return null;
  }

  /**
   * Force the extension approval popup. onlyIfTrusted MUST be false on this path.
   */
  async function invokeConnect(provider) {
    if (!provider) {
      const err = new Error("No wallet provider");
      err.code = "provider_missing";
      throw err;
    }
    const opts = { onlyIfTrusted: false };
    if (typeof provider.connect === "function") {
      return provider.connect(opts);
    }
    if (typeof provider.request === "function") {
      return provider.request({ method: "connect", params: opts });
    }
    const err = new Error("Wallet provider has no connect() method");
    err.code = "no_connect";
    throw err;
  }

  /**
   * MUST be called directly from a click/tap handler.
   * Do NOT await anything (provider wait, timers, fetch) before invokeConnect —
   * browsers drop the user gesture and Phantom/Solflare will not show a popup.
   */
  async function connectProvider(name, options) {
    const opts = options || {};
    const { phantom, solflare } = detectProviders();
    let provider = null;
    if (name === "phantom") provider = phantom;
    else if (name === "solflare") provider = solflare;
    if (!provider) {
      // Optional late inject: only if caller explicitly allows (still may lose popup).
      if (opts.allowWait && opts.waitMs !== 0) {
        await waitForProviders(opts.waitMs != null ? opts.waitMs : 300);
        const again = detectProviders();
        provider = name === "phantom" ? again.phantom : again.solflare;
      }
    }
    if (!provider) {
      const err = new Error(providerMissingMessage(name));
      err.code = "provider_missing";
      err.installUrl = name === "phantom" ? PHANTOM_INSTALL : SOLFLARE_INSTALL;
      throw err;
    }
    // First await must be connect() itself so the extension approval UI can open.
    const resp = await invokeConnect(provider);
    const pk = extractPubkey(resp, provider);
    if (!pk) {
      const err = new Error("Wallet connected but no public key returned");
      err.code = "no_pubkey";
      throw err;
    }
    saveStored(name, pk);
    return { provider: name, pubkey: pk, adapter: provider };
  }

  /**
   * Silent restore for page load only. Does NOT show a popup.
   * Returns null if not previously trusted / not injected yet.
   */
  async function tryEagerReconnect(name) {
    await waitForProviders(800);
    const { phantom, solflare } = detectProviders();
    const provider = name === "solflare" ? solflare : phantom;
    if (!provider || typeof provider.connect !== "function") return null;
    try {
      const resp = await provider.connect({ onlyIfTrusted: true });
      const pk = extractPubkey(resp, provider);
      if (!pk) return null;
      saveStored(name, pk);
      return { provider: name, pubkey: pk, adapter: provider };
    } catch (_) {
      return null;
    }
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
    STORAGE_KEY: STORAGE_KEY,
    PHANTOM_INSTALL: PHANTOM_INSTALL,
    SOLFLARE_INSTALL: SOLFLARE_INSTALL,
    shortPubkey: shortPubkey,
    detectProviders: detectProviders,
    waitForProviders: waitForProviders,
    loadStored: loadStored,
    saveStored: saveStored,
    clearStored: clearStored,
    connectProvider: connectProvider,
    tryEagerReconnect: tryEagerReconnect,
    disconnectProvider: disconnectProvider,
    providerMissingMessage: providerMissingMessage,
  };
})(typeof window !== "undefined" ? window : globalThis);
