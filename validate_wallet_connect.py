"""Validate Phantom/Solflare Connect helpers force extension connect() popup."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
WALLET_JS = PROJECT_ROOT / "static" / "wallet_connect.js"
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"


def test_wallet_js_exists_and_parses():
    assert WALLET_JS.is_file(), f"missing {WALLET_JS}"
    proc = subprocess.run(
        ["node", "--check", str(WALLET_JS)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"wallet_connect.js syntax error:\n{proc.stderr}"
    print("PASS: wallet_connect.js parses")


def test_connect_forces_popup_opts():
    src = WALLET_JS.read_text(encoding="utf-8")
    assert "onlyIfTrusted: false" in src, "user connect path must force popup"
    assert "onlyIfTrusted: true" in src, "eager restore may use onlyIfTrusted true"
    assert "provider.connect(opts)" in src
    assert "async function invokeConnect" in src
    assert "phantom.app/download" in src
    # User-facing connect goes through invokeConnect (popup); eager path is separate.
    invoke_fn = src[src.find("async function invokeConnect") :]
    invoke_fn = invoke_fn[: invoke_fn.find("async function connectProvider")]
    assert "onlyIfTrusted: false" in invoke_fn
    assert "onlyIfTrusted: true" not in invoke_fn
    assert "await invokeConnect(provider)" in src
    eager_fn = src[src.find("async function tryEagerReconnect") :]
    assert "onlyIfTrusted: true" in eager_fn
    print("PASS: connectProvider forces onlyIfTrusted:false")


def test_index_wires_connect_click():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'src="/static/wallet_connect.js' in html
    assert "function onWalletConnectClick" in html
    assert 'bindClick("btnWalletConnect"' in html
    assert "onWalletConnectClick(ev)" in html
    assert 'bindClick("btnConnectPhantom"' in html
    assert 'bindClick("btnConnectSolflare"' in html
    assert "connectBrowserWallet(" in html
    # Must not disable wallet menu buttons (disabled = no click = no connect())
    assert "phantomBtn.disabled = false" in html
    assert "solflareBtn.disabled = false" in html
    assert "phantomBtn.disabled = !providers.phantom" not in html
    print("PASS: index.html Connect handlers call connect()")


def test_node_mock_connect_called():
    """Simulate providers and assert connect({ onlyIfTrusted: false }) is invoked."""
    script = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let phantomConnectCalls = [];
let solflareConnectCalls = [];

const fakeWindow = {
  phantom: {
    solana: {
      isPhantom: true,
      connect(opts) {
        phantomConnectCalls.push(opts);
        return Promise.resolve({ publicKey: { toString: () => "PhanPubKey111" } });
      },
      disconnect() { return Promise.resolve(); },
    },
  },
  solflare: {
    isSolflare: true,
    connect(opts) {
      solflareConnectCalls.push(opts);
      return Promise.resolve({ publicKey: { toString: () => "SolfPubKey222" } });
    },
    disconnect() { return Promise.resolve(); },
  },
  localStorage: {
    _d: {},
    getItem(k) { return this._d[k] || null; },
    setItem(k, v) { this._d[k] = String(v); },
    removeItem(k) { delete this._d[k]; },
  },
  addEventListener() {},
  removeEventListener() {},
};
fakeWindow.window = fakeWindow;

const code = fs.readFileSync(path.join("static", "wallet_connect.js"), "utf8");
vm.runInNewContext(code, { window: fakeWindow, globalThis: fakeWindow });

(async () => {
  const WC = fakeWindow.SolDexWalletConnect;
  if (!WC) throw new Error("SolDexWalletConnect missing");
  const r1 = await WC.connectProvider("phantom", { waitMs: 50 });
  if (r1.pubkey !== "PhanPubKey111") throw new Error("bad phantom pubkey");
  if (phantomConnectCalls.length !== 1) throw new Error("phantom.connect not called");
  if (!phantomConnectCalls[0] || phantomConnectCalls[0].onlyIfTrusted !== false) {
    throw new Error("phantom.connect must use onlyIfTrusted:false got " + JSON.stringify(phantomConnectCalls[0]));
  }
  const r2 = await WC.connectProvider("solflare", { waitMs: 50 });
  if (r2.pubkey !== "SolfPubKey222") throw new Error("bad solflare pubkey");
  if (!solflareConnectCalls[0] || solflareConnectCalls[0].onlyIfTrusted !== false) {
    throw new Error("solflare.connect must use onlyIfTrusted:false");
  }
  // Missing provider message
  delete fakeWindow.phantom;
  fakeWindow.solana = undefined;
  try {
    await WC.connectProvider("phantom", { waitMs: 50 });
    throw new Error("expected provider_missing");
  } catch (e) {
    if (!e || e.code !== "provider_missing") throw new Error("expected provider_missing code got " + (e && e.code));
    if (!/Install Phantom|Phantom extension/i.test(e.message)) throw new Error("bad missing msg: " + e.message);
  }
  console.log("PASS: mock connect() called with onlyIfTrusted:false");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
"""
    tmp = PROJECT_ROOT / "_test_wallet_connect_mock.js"
    tmp.write_text(script, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["node", str(tmp)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        if tmp.exists():
            tmp.unlink()
    assert proc.returncode == 0, f"mock connect test failed:\n{proc.stdout}\n{proc.stderr}"
    assert "PASS:" in proc.stdout
    print(proc.stdout.strip())


def main() -> int:
    test_wallet_js_exists_and_parses()
    test_connect_forces_popup_opts()
    test_index_wires_connect_click()
    test_node_mock_connect_called()
    print("All wallet connect validations passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
