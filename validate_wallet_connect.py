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
    assert "function beginUserConnect" in src, "sync click entry beginUserConnect required"
    assert "function startConnect" in src
    assert "phantom.app/download" in src
    # User-facing sync path must call connect before any await.
    begin_fn = src[src.find("function beginUserConnect") :]
    begin_fn = begin_fn[: begin_fn.find("async function connectProvider")]
    assert "startConnect(provider)" in begin_fn
    assert "await waitForProviders" not in begin_fn
    assert "onlyIfTrusted: true" not in begin_fn
    start_fn = src[src.find("function startConnect") :]
    start_fn = start_fn[: start_fn.find("function beginUserConnect")]
    assert "onlyIfTrusted: false" in start_fn
    assert "provider.connect(opts)" in start_fn
    eager_fn = src[src.find("async function tryEagerReconnect") :]
    assert "onlyIfTrusted: true" in eager_fn
    print("PASS: beginUserConnect forces onlyIfTrusted:false (sync)")


def test_index_wires_connect_click():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'src="/static/wallet_connect.js' in html
    assert "function onWalletConnectClick" in html
    assert 'bindClick("btnWalletConnect"' in html
    assert "onWalletConnectClick(ev)" in html
    assert 'bindClick("btnConnectPhantom"' in html
    assert 'bindClick("btnConnectSolflare"' in html
    assert "connectBrowserWallet(" in html
    assert "beginUserConnect" in html
    # Must not disable wallet menu buttons (disabled = no click = no connect())
    assert "phantomBtn.disabled = false" in html
    assert "solflareBtn.disabled = false" in html
    assert "phantomBtn.disabled = !providers.phantom" not in html
    # Cache bust must be bumped when wallet_connect.js changes
    assert "wallet_connect.js?v=1.1.4" in html or re.search(
        r"wallet_connect\.js\?v=1\.\d+\.\d+", html
    ), "wallet_connect.js cache query missing"
    print("PASS: index.html Connect handlers call beginUserConnect/connect()")


def test_node_mock_connect_called():
    """Simulate providers and assert connect({ onlyIfTrusted: false }) is invoked sync."""
    script = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let phantomConnectCalls = [];
let solflareConnectCalls = [];
let phantomDisconnectCalls = 0;

const fakeWindow = {
  phantom: {
    solana: {
      isPhantom: true,
      connect(opts) {
        phantomConnectCalls.push(opts);
        return Promise.resolve({ publicKey: { toString: () => "PhanPubKey111" } });
      },
      disconnect() { phantomDisconnectCalls++; return Promise.resolve(); },
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
  if (typeof WC.beginUserConnect !== "function") throw new Error("beginUserConnect missing");

  // Sync path: connect() must already have been called when beginUserConnect returns.
  const p1 = WC.beginUserConnect("phantom");
  if (phantomConnectCalls.length !== 1) throw new Error("phantom.connect not called sync");
  if (!phantomConnectCalls[0] || phantomConnectCalls[0].onlyIfTrusted !== false) {
    throw new Error("phantom.connect must use onlyIfTrusted:false got " + JSON.stringify(phantomConnectCalls[0]));
  }
  const r1 = await p1;
  if (r1.pubkey !== "PhanPubKey111") throw new Error("bad phantom pubkey");

  const p2 = WC.beginUserConnect("solflare");
  if (solflareConnectCalls.length !== 1) throw new Error("solflare.connect not called sync");
  if (!solflareConnectCalls[0] || solflareConnectCalls[0].onlyIfTrusted !== false) {
    throw new Error("solflare.connect must use onlyIfTrusted:false");
  }
  const r2 = await p2;
  if (r2.pubkey !== "SolfPubKey222") throw new Error("bad solflare pubkey");

  await WC.disconnectProvider("phantom");
  if (phantomDisconnectCalls < 1) throw new Error("disconnect not called");
  if (WC.loadStored()) throw new Error("stored wallet should be cleared");

  // Missing provider message
  delete fakeWindow.phantom;
  fakeWindow.solana = undefined;
  try {
    WC.beginUserConnect("phantom");
    throw new Error("expected provider_missing");
  } catch (e) {
    if (!e || e.code !== "provider_missing") throw new Error("expected provider_missing code got " + (e && e.code));
    if (!/Install Phantom|Phantom extension/i.test(e.message)) throw new Error("bad missing msg: " + e.message);
  }
  console.log("PASS: mock beginUserConnect() called with onlyIfTrusted:false (sync)");
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
