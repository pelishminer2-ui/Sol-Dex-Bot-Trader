/**
 * Phantom / Solflare browser-extension connect helpers.
 * Extension connect verifies the wallet in the browser; Live bot auto-sign
 * still requires Set Wallet (base58) so the Flask server can sign swaps.
 *
 * Critical: user-initiated Connect MUST call provider.connect({ onlyIfTrusted: false })
 * SYNCHRONOUSLY inside the click handler (before any await / timer). Nested async/await
 * can drop transient user activation → Phantom shows no approval popup.
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
      // Prefer Phantom's dedicated namespace (multi-wallet safe).
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

  function resolveProvider(name) {
    const { phantom, solflare } = detectProviders();
    if (name === "phantom") return phantom;
    if (name === "solflare") return solflare;
    return null;
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
   * NEVER call this before user-gesture connect().
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
   * Start extension connect() NOW (sync). onlyIfTrusted MUST be false.
   * Returns the raw Promise from provider.connect — do not wrap in async
   * before this call or the Phantom popup may never appear.
   */
  function startConnect(provider) {
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
   * MUST be called directly from a click/tap handler (sync function preferred).
   * Invokes provider.connect() in this turn, then returns a Promise for pubkey.
   * Do NOT await waitForProviders / timers / fetch before this.
   */
  function beginUserConnect(name) {
    const provider = resolveProvider(name);
    if (!provider) {
      const err = new Error(providerMissingMessage(name));
      err.code = "provider_missing";
      err.installUrl = name === "phantom" ? PHANTOM_INSTALL : SOLFLARE_INSTALL;
      throw err;
    }
    // First line that talks to the extension — must stay sync relative to click.
    const connectPromise = startConnect(provider);
    return Promise.resolve(connectPromise).then(function (resp) {
      const pk = extractPubkey(resp, provider);
      if (!pk) {
        const err = new Error("Wallet connected but no public key returned");
        err.code = "no_pubkey";
        throw err;
      }
      saveStored(name, pk);
      return { provider: name, pubkey: pk, adapter: provider };
    });
  }

  /**
   * Async wrapper kept for callers/tests. Prefer beginUserConnect from UI clicks.
   * Never awaits before connect unless allowWait is explicitly set (may lose popup).
   */
  async function connectProvider(name, options) {
    const opts = options || {};
    let provider = resolveProvider(name);
    if (!provider && opts.allowWait && opts.waitMs !== 0) {
      await waitForProviders(opts.waitMs != null ? opts.waitMs : 300);
      provider = resolveProvider(name);
    }
    if (!provider) {
      const err = new Error(providerMissingMessage(name));
      err.code = "provider_missing";
      err.installUrl = name === "phantom" ? PHANTOM_INSTALL : SOLFLARE_INSTALL;
      throw err;
    }
    const resp = await startConnect(provider);
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
    const provider = resolveProvider(name === "solflare" ? "solflare" : "phantom");
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

  /**
   * Disconnect extension + clear stored pubkey. Always clears storage even if
   * provider.disconnect hangs or throws — UI must not get stuck Connected.
   */
  function disconnectProvider(name) {
    const provider = resolveProvider(name === "solflare" ? "solflare" : "phantom");
    clearStored();
    let disc = Promise.resolve();
    try {
      if (provider && typeof provider.disconnect === "function") {
        disc = Promise.resolve(provider.disconnect()).catch(function () {});
      }
    } catch (_) {}
    const timerFn = typeof global.setTimeout === "function" ? global.setTimeout : null;
    if (!timerFn) return disc;
    // Bound wait so Disconnect never blocks the UI forever.
    return Promise.race([
      disc,
      new Promise(function (resolve) {
        timerFn(resolve, 1500);
      }),
    ]);
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
    beginUserConnect: beginUserConnect,
    connectProvider: connectProvider,
    tryEagerReconnect: tryEagerReconnect,
    disconnectProvider: disconnectProvider,
    providerMissingMessage: providerMissingMessage,
  };
})(typeof window !== "undefined" ? window : globalThis);
