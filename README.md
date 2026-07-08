# Solana Mover Trading Bot

## Local-only (git)

This project is **local-only**: no git remote is configured. All files and fixes stay under C:\Users\Owner\Desktop\Solana. See [LOCAL_ONLY.md](LOCAL_ONLY.md).

## Project location

All bot files live at **`C:\Users\Owner\Desktop\Solana`**. Run every command from this directory.

| Item | Path |
|------|------|
| Project root | `C:\Users\Owner\Desktop\Solana` |
| Web GUI | `http://127.0.0.1:5000` |
| Trade journal | `trades.jsonl` (project root) |
| Session P&L | `session_pnl.json` (project root) |
| Tax CSV | `tax_trades.csv` (project root) |
| VS Code workspace | `Solana.code-workspace` |

Data paths in `.env` are relative to the project root and resolved to absolute paths at runtime via `PROJECT_ROOT` in `config.py`.

---

Automated momentum trading bot for Solana that scans DexScreener top gainers, **pump.fun tokens**, **Birdeye trending gems**, and **GMGN.ai Solana trending**, buys on **+0.5%** price moves (profit-first, not scalp noise), and exits via a 4-part take-profit ladder (+1.5%, +3%, +7%, +10%) with instant full exit at **+5%**, global trend-weakening at **â‰¥2%**, or stop-loss via Jupiter swaps.

## Profit-first philosophy

The bot is tuned to **target real net profit after fees**, not break-even scalps:

- **Minimum expected net edge** â€” entries are skipped unless the fee-adjusted ladder can deliver at least `MIN_EXPECTED_NET_PROFIT_SOL` (default **0.0155 SOL**).
- **Stronger momentum filter** â€” default entry trigger is **+0.5%** (`ENTRY_MOMENTUM_PCT=0.005`), configurable in the GUI.
- **Pool liquidity minimum** â€” DEX pair liquidity below **$12k** is filtered at scan and entry (configurable via `MIN_LIQUIDITY_USD`; live wallet funding requirement is **0.75 SOL** only).
- **Entry impact cap** â€” Jupiter buy quotes with **>1% price impact** are rejected (`MAX_ENTRY_PRICE_IMPACT_PCT=1.0`).
- **Breakeven protection** â€” after L1 partial take-profit (25% sold), stop-loss on the remaining **75%** moves to **entry price** so winners cannot revert to losses.
- **Early ladder exit on momentum slowdown** â€” after L2 or L3 partial sells, if price momentum is fading (recent move < 50% of peak, or consecutive poll decline), the bot sells **all remaining** instead of waiting for L4.
- **Instant +5% profit exit** â€” at any point after entry, if unrealized PnL reaches **â‰¥5%**, the bot sells **100% of remaining** immediately (`INSTANT_PROFIT_EXIT_PCT=0.05`), after L1/L2 partials but before L3/L4.
- **Global trend-weakening exit** â€” at any point during an open position, if unrealized PnL is **â‰¥2%** and trend momentum is weakening, the bot sells **100% of remaining** immediately (`WEAKEN_EXIT_MIN_PROFIT_PCT=0.02`).
- **Loss brakes** â€” optional daily loss cap (`MAX_DAILY_LOSS_SOL`) and tighter default stop-loss (**1.5%**). Consecutive-loss pause is **disabled by default** (`MAX_CONSECUTIVE_LOSSES=0`); set to e.g. `3` to pause new entries after N losses in a row.

**Realistic caveat:** No strategy eliminates all losses. Thin pools, rugs, and adverse moves can still produce losing trades. Profit-first filters reduce marginal entries and protect partial winners, but live trading always carries risk.

## Features

- DexScreener mover scanner with liquidity, volume, and pool-age filters
- **Pump.fun token scanner** â€” merges pump.fun launches with DexScreener movers into one watchlist (toggle via `INCLUDE_PUMPFUN` or GUI checkbox)
- **Birdeye Find Gems scanner** â€” merges [Birdeye find-gems](https://birdeye.so/solana/find-gems) **1h % gainers** (mint/CA addresses) into the unified watchlist (toggle via `SCAN_BIRDEYE` or GUI checkbox)
- **GMGN.ai scanner** â€” merges [GMGN Solana trending](https://gmgn.ai/?chain=sol) tokens (skills market at [gmgn.ai/ai](https://gmgn.ai/ai?chain=sol&tab=skills_market) is for Agent API keys; token CAs come from the quotation rank API) into the unified watchlist (toggle via `SCAN_GMGN` / `GMGN_ENABLED` or GUI checkbox)
- **Pinned watchlist mint** â€” optionally tracks a specific mint (`WATCHLIST_MINT`) and adds it as a trade candidate when USD price rises **â‰¥ `WATCHLIST_MIN_USD_GAIN`** (default **$75**) from the rolling baseline (`BASELINE_WINDOW_SEC`); same ladder/exit rules apply
- Rolling baseline momentum detection (+0.5% entry trigger by default)
- Laddered take-profit (+1.5% / +3% / +7% / +10%, 25% each); selectable stop-loss (-1.5%, -3.0%, or -5.0%), breakeven SL after L1, instant full exit at **+5% profit**, global trend-weakening exit at **â‰¥2% profit**, early full exit on momentum slowdown after L2/L3, and time-stop exits
- Similarity-based re-entry after profitable trades
- **Multi-position nonstop trading** â€” hold up to **2** different tokens concurrently; the bot keeps scanning and entering every `PRICE_POLL_SEC` while slots remain open
- **Dip re-entry** â€” re-buy a previously traded mint when price drops **-8%** from its last exit (`REENTRY_DIP_PCT=0.08`); bypasses normal trade cooldown
- Jupiter v1 swap execution (SOL â†” token) via `lite-api.jup.ag`
- Dry-run / paper trade mode for safe testing (no real Jupiter swaps); paper mode uses a **simulated 0.75 SOL wallet** (`PAPER_SIMULATED_BALANCE_SOL`) for sizing and funding checks so you can trade without a funded wallet
- **24-hour paper sessions** â€” paper mode auto-stops after `PAPER_SESSION_HOURS` (default 24h) with session profit/loss totals; the **Paper Session â€” 24h Test Period** panel (above Tax Records in the GUI) shows session status, countdown, P&L, recent paper trades, and a session-only CSV export
- **Paper balance depletion stop** â€” paper mode tracks a running simulated SOL balance (starts at `PAPER_SIMULATED_BALANCE_SOL`, default 0.75). Buys subtract `sol_in`, sells add `sol_out`. If simulated SOL runs out before the 24h session ends, the bot auto-stops with final P&L stats frozen on the dashboard (`paper_stop_reason: balance_depleted`)
- **Running P&L** â€” cumulative profit, losses, and **Net Profit (after fees)** update after every sell (partial ladder or full exit) in both paper and live modes; fees are tracked separately and not counted as profit
- Trade size set via GUI **Trade Size (SOL)** dropdown (0.05â€“1.0 SOL); backend applies a fixed safety cap (`MAX_WALLET_TRADE_PCT`, default 75%)
- Trade journal (`trades.jsonl`)
- Tax export CSV (`tax_trades.csv`) for live sells only â€” wallet address, contract mint, PnL per exit

## Quick Start

### Automated trading (GUI â€” recommended)

**Fastest start:** double-click **`C:\Users\Owner\Desktop\Solana\Start Bot.bat`** â€” that's it. No Cursor or terminal needed. It bootstraps `.venv` on first run, starts the Flask server, opens your browser to **http://127.0.0.1:5000**, and **stops the server when you close the dashboard browser tab/window or close the launcher window**. Launch scripts live on disk and are **not** served by Flask; an old Desktop shortcut or duplicate folder can run stale code. See [Standalone App](#standalone-app) for desktop shortcuts, hidden launch, auto-start, PWA install, and optional login watchdog.

1. **Install and run the dashboard**
   ```bash
   cd C:\Users\Owner\Desktop\Solana
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
   Or use **`Start Bot.bat`** instead of `python app.py` â€” it handles server startup, opens the browser, and stops the server when you close the browser or launcher window.
2. Open **http://127.0.0.1:5000** in your browser (skipped if you used `Start Bot.bat`).
3. **Paper mode (no wallet needed):** leave **Paper Trade** checked â†’ click **Start Bot**. The dashboard shows **Paper Balance: 0.75 SOL (simulated)** â€” the bot sizes trades from your **Trade Size (SOL)** selection and reserve rules without requiring real funds. The balance decreases on paper buys and increases on sells; if it runs out before the 24h session ends, the bot stops automatically and keeps final session stats visible. The bot scans DexScreener, pump.fun, and Birdeye every `SCAN_INTERVAL_SEC`, monitors prices every `PRICE_POLL_SEC`, and automatically paper-buys on +0.5% momentum (or -8% dip re-entry) and exits via the 4-part TP ladder or stop-loss. Status shows **Running - Scanning** or **Running - In Trade**.
4. **Live mode:** paste your wallet private key â†’ click **Set Wallet** â†’ uncheck **Paper Trade** â†’ ensure balance â‰¥ **0.75 SOL** â†’ click **Start Bot**. The live checklist shows what's missing before you start.

API equivalent for paper start:
```bash
curl -X POST http://127.0.0.1:5000/api/bot/start -H "Content-Type: application/json" -d "{\"paper_trade\": true}"
```

If Start says "already running" but the bot isn't trading, click **Stop** or call `POST /api/bot/force-reset`, then Start again.

### 1. Install dependencies

```bash
cd C:\Users\Owner\Desktop\Solana
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
copy .env.example .env
```

Edit `.env`:

- `SOLANA_PRIVATE_KEY` â€” base58 or JSON array (dedicated hot wallet only)
- `SOLANA_RPC_URL` â€” paid RPC recommended for mainnet (Helius, QuickNode)
- Scanner API keys â€” see [Scanner API keys](#scanner-api-keys) below
- Keep `DRY_RUN=true` until you verify behavior

**Never commit `.env` or share your private key.**

### 3. Run dry-run (recommended first)

```bash
python main.py --dry-run
```

Expected output:

- Startup banner with wallet, network, strategy params
- Periodic "Scanner found N qualified movers", "Scanner found N pump.fun tokens", and "Scanner found N birdeye gems" logs
- `[DRY RUN] BUY` / `[DRY RUN] SELL` when signals fire

### 4. Validation checklist

Before any live trade:

- [ ] Run `python validate_security_firewall.py` to verify localhost firewall, allowlist, and trading lock
- [ ] Run `python validate_transfer_guard.py` to verify transfer guard blocks unauthorized sends
- [ ] Run `python validate_multi_position.py` to verify 2-position and dip re-entry logic
- [ ] Verify `trades.jsonl` records buy/sell events
- [ ] Confirm wallet has at least **0.75 SOL** (`MIN_FUND_SOL`) for live trading, plus fees and `TRADE_SIZE_SOL`
- [ ] Set a paid mainnet RPC URL
- [ ] Start with `TRADE_SIZE_SOL=0.01` for first live round-trip
- [ ] Run live: `python main.py --live`

### 5. Live trading

```bash
python main.py --live
```

Press `Ctrl+C` to stop. The bot attempts to close all open positions on shutdown.

## Web GUI (browser dashboard)

Run the browser-based control panel on localhost:

```bash
python app.py
```

Or:

```bash
python gui.py
```

Open **http://127.0.0.1:5000** in your browser.

### GUI features

- **Wallet setup** â€” paste a base58 secret key or JSON byte array (masked input). Key stays in server memory only for the session. Shows minimum **0.75 SOL** funding requirement for live mode.
- **Bot controls** â€” Start / Stop with a **Paper Trade** checkbox (checked by default). Paper mode runs the full scan â†’ signal â†’ buy/sell flow without executing real swaps, using a simulated **0.75 SOL** wallet (`PAPER_SIMULATED_BALANCE_SOL`) for trade sizing and funding checks; the dashboard **Paper Balance** updates live as trades execute. **Stop Bot** in paper mode resets simulated SOL to your configured paper target. Paper sessions default to **24 hours** (`PAPER_SESSION_HOURS`); the bot auto-stops when the session expires or when simulated SOL is depleted, and shows cumulative session profit, losses, net P&L, and trade count. **Running P&L** panel shows live-updating session totals and the last 10 sell contributions in both paper and live modes (resets only when you press Start). **Include Pump.fun tokens** and **Include Birdeye gems** checkboxes (both checked by default) merge those sources into the watchlist. Uncheck for Live Trading; start is blocked if balance is below `MIN_FUND_SOL` (0.75 SOL).
- **Config panel** â€” adjust trade size (dropdown: 0.05, 0.07, 0.10, 0.20, 0.30, 0.50, or 1.00 SOL â€” capped by the 15% wallet rule; on the 0.75 SOL paper wallet larger selections are limited to ~0.11 SOL until balance is higher), entry momentum %, stop loss (dropdown: 1.5%, 2.0%, 3.0%, or 5.0%), and RPC URL. Take-profit ladder (+3% / +4%, 50% each by default) is shown read-only with **Target Net Profit**, **Est. Fees**, and gross ladder %. **Best Win** preset button applies the full fee-aware strategy (0.10 SOL, 0.50% entry, 2% stop-loss, 0.002 SOL min net) and saves a revert bookmark. **Revert to bookmark** restores the prior snapshot (`presets/win_focused_bookmark.json`). Use **Reset Spreads to Defaults** for code defaults. Most params apply without restart. Skip reasons appear in **Last Action** when entries are blocked.
- **Live dashboard** â€” wallet address, SOL balance, top 10 movers (with `pumpfun` / `birdeye` / `dex` source badges), pump.fun and Birdeye scan counts, up to **2 open positions** with PnL (`Positions: N/2`), **Trade CLI** feed (per-trade buy/sell lines), recent trades, bot status, and log stream.
- **Paper session panel** â€” above Tax Records, shows the current 24h paper test period: status (Active / Ended / Not started), countdown, session profit/loss/net, trade count, recent paper trades, and **Download Paper Session CSV** (paper exits only; separate from live tax CSV). Data via `GET /api/bot/status`, `GET /api/paper/session`, and `GET /api/paper/export`.
- **Tax bookkeeping** â€” live sells are appended to `tax_trades.csv` (configurable via `TAX_CSV_PATH`). Paper/dry-run trades are excluded. On every live sell the bot automatically rebuilds `tax_summary_monthly.csv` and `tax_summary_yearly.csv` with profit, loss, net PnL, and trade counts grouped by UTC month and year. CSV files use UTF-8 with BOM (`utf-8-sig`) so Excel opens them cleanly. Export from the dashboard or API:
  - `GET /api/tax/export?format=csv&report=trades` â€” trade log (default)
  - `GET /api/tax/export?format=csv&report=monthly|yearly` â€” summary files
  - `GET /api/tax/export?format=xlsx&report=trades|monthly|yearly` â€” Excel workbook (requires `openpyxl`)
  - `GET /api/tax/summary` â€” current month/year breakdown plus monthly (last 12) and yearly tables
  - `GET /api/tax/preview` â€” last 20 trade rows plus totals and summary previews
  The trades CSV ends with a single `SUMMARY` footer row. This is a record-keeping aid only â€” not tax advice.
- **Save to .env** â€” optional button (with confirmation) to persist the session key to `.env`.
- **Branding** â€” on each page load the GUI randomly picks one of three partner logos (`static/assets/`) as a subtle backdrop watermark, header logo, and browser favicon. `/favicon.ico` also returns a random logo on each request.

### GUI security / firewall

The web dashboard includes a **request firewall** (`security_firewall.py`) and a **trading execution lock** (`trading_lock.py`) to reduce the risk of remote wallet drain or swap injection:

| Layer | Protection |
|-------|------------|
| **Network bind** | Flask binds to `127.0.0.1` only by default (`FLASK_HOST`). A loud stderr warning appears if you set `0.0.0.0`. |
| **Request firewall** | Rejects non-localhost clients (`127.0.0.1`, `::1`). `X-Forwarded-For` is distrusted unless `TRUST_X_FORWARDED_FOR=true`. |
| **API allowlist** | Only documented GET/POST routes are permitted; everything else returns **403**. |
| **Rate limiting** | `FIREWALL_RATE_LIMIT` requests per minute per IP (default 120). |
| **Swap injection block** | No API route accepts raw transaction bytes, arbitrary mint swaps, or private-key export. |
| **Trading lock** | Only the bot strategy thread may call `jupiter.execute_quote()` while the bot is running. Flask routes are control-only (start/stop/config). |
| **Transfer guard** | `tx_authorizer.py` issues one-time tokens for each authorized Jupiter swap. `send_versioned_transaction()` refuses to sign bare SOL drains, unchecked SPL transfers, or any tx without a valid token. Requires `trading_lock` + watchlist/position mint. Set `ENFORCE_TRANSFER_GUARD=true` (default). Disabling in live mode is strongly discouraged. |
| **CORS** | Restricted to `http://127.0.0.1:5000` and `http://localhost:5000` â€” no wildcard `*`. |

**Never expose port 5000 publicly.** Do not port-forward, tunnel without auth, or bind `FLASK_HOST=0.0.0.0` on an internet-facing machine.

Optional Windows defense-in-depth (run as Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\allow_localhost_only.ps1
```

Validate firewall behavior:

```bash
python validate_security_firewall.py
python validate_transfer_guard.py
```

### GUI security notes

- **Do not expose port 5000 publicly.** Bind stays on `127.0.0.1` by default (`FLASK_HOST`).
- Private keys are never logged or returned in API responses.
- Use "Save to .env" only on a trusted machine.

Configure host/port via environment:

```bash
set FLASK_HOST=127.0.0.1
set GUI_PORT=5000
python app.py
```

`GUI_HOST` is still accepted as a fallback alias for `FLASK_HOST`.

The CLI entry point (`python main.py`) is unchanged and works independently of the GUI.

### Standalone App

**No Cursor, VS Code, or terminal required.** The bot is a normal Windows app: double-click **`Start Bot.bat`** in `C:\Users\Owner\Desktop\Solana`, and it creates the Python environment on first run (if needed), starts the Flask server, and opens your browser to the dashboard.

#### One-click launcher (recommended)

| File | Purpose |
|------|---------|
| **`Start Bot.bat`** | **THE ONE FILE TO CLICK** â€” bootstrap, start server, open browser, stop server when session ends |
| `launch.bat` | Thin wrapper â†’ `Start Bot.bat` (kept for old shortcuts) |
| `launch.cmd` | Thin wrapper â†’ `Start Bot.bat` |
| `launch.ps1` | PowerShell logic used by `Start Bot.bat` |
| `run_hidden.vbs` | Advanced: no console window; server stays running after browser opens |
| `install.bat` | First-time setup only: create `.venv` + `pip install` |
| `stop.bat` | Emergency stop for the background Flask server |
| `install_startup.bat` | Auto-start at Windows login (uses `run_hidden.vbs`) |
| `uninstall_startup.bat` | Remove login auto-start shortcut |

**Fastest start:** double-click **`C:\Users\Owner\Desktop\Solana\Start Bot.bat`**. First run may take a few minutes while dependencies install into `.venv` in the project folder.

What `Start Bot.bat` does:

1. Runs `install.ps1` if `.venv` is missing or incomplete (creates venv, `pip install -r requirements.txt`).
2. Single-instance guard â€” reuses a healthy server on port **5000** or starts `.venv\Scripts\python app.py` hidden in the background.
3. Polls `/api/bot/status` until HTTP 200.
4. Opens your default browser to **http://127.0.0.1:5000**.
5. **Session mode:** waits until you close the dashboard browser tab/window (or close the launcher window), then stops the Flask server it started. If the server was already running from an earlier session, closing the browser leaves it running â€” use `stop.bat` to stop manually.

**Desktop shortcut:** right-click `Start Bot.bat` â†’ **Send to** â†’ **Desktop (create shortcut)**. In shortcut **Properties**, set **Target** to `C:\Users\Owner\Desktop\Solana\Start Bot.bat` and **Start in** to `C:\Users\Owner\Desktop\Solana`. Pin to the taskbar if you like.

**Stop the server:** close the browser tab/window or the `Start Bot.bat` console window (if this session started the server), or double-click `stop.bat`, or close the Python process from Task Manager.

**Hidden launch (no black console, server keeps running):** double-click `run_hidden.vbs` instead of `Start Bot.bat`.

`app.py` refuses to start a second server if port 5000 is already serving the bot, so double-clicking the launcher while the server is running simply opens the browser.

#### Install as a PWA (Chrome / Edge)

1. Start the server (`Start Bot.bat` or `python app.py`).
2. Open **http://127.0.0.1:5000**.
3. Click the **Install app** icon in the address bar (or menu â†’ **Apps** â†’ **Install Solana Mover Trading Bot**).

The installed app opens in its own window (`display: standalone`) with the Solana green theme. It still requires the Flask server on localhost â€” use `Start Bot.bat` first, or the watchdog below.

#### Optional: auto-start at Windows login

**Easy way:** double-click `install_startup.bat`. This adds a shortcut to your Windows **Startup** folder that runs `run_hidden.vbs` (server only, no console, no browser until you open the dashboard).

Remove it anytime with `uninstall_startup.bat`.

#### Optional: watchdog at Windows login

`watchdog.py` is a small background process that restarts Flask if port 5000 goes down. It does **not** open a browser.

```bash
cd C:\Users\Owner\Desktop\Solana
.venv\Scripts\python watchdog.py
```

**Task Scheduler setup (run once):**

1. Open **Task Scheduler** â†’ **Create Task**.
2. **General:** name *Solana Bot Watchdog*, run only when user is logged on.
3. **Triggers:** **At log on** (your user).
4. **Actions:** **Start a program**
   - Program: `C:\Users\Owner\Desktop\Solana\.venv\Scripts\pythonw.exe`
   - Arguments: `watchdog.py`
   - Start in: `C:\Users\Owner\Desktop\Solana`
5. **Settings:** allow task to run on demand; do not stop if running longer than 3 days.

Use `pythonw.exe` (no console window). Adjust paths if your project lives elsewhere.

Environment variables:

| Variable | Default | Used by |
|----------|---------|---------|
| `SOLANA_AUTO_OPEN_BROWSER` | `1` for manual `python app.py`, `0` when launched via `Start Bot.bat` / watchdog | `app.py` |
| `SOLANA_LAUNCHED_BY` | unset (`launcher` or `watchdog` when set) | `app.py` |
| `GUI_PORT` / `FLASK_PORT` | `5000` | `app.py`, launchers, watchdog |
| `WATCHDOG_INTERVAL_SEC` | `30` | `watchdog.py` |

Browsers cannot start Python on first visit for security reasons â€” the launcher and optional watchdog provide the automatic server experience.

## Strategy

| Parameter | Default | Description |
|-----------|---------|-------------|
| `ENTRY_MOMENTUM_PCT` | 0.005 | Buy when price rises +0.5% from baseline |
| `MIN_EXPECTED_NET_PROFIT_SOL` | 0.001 | Skip entry if fee-adjusted expected ladder net profit is below this |
| `TARGET_NET_PROFIT_SOL` | 0.0155 | Net SOL profit target per completed ladder (after fees) |
| `MAX_ENTRY_PRICE_IMPACT_PCT` | 1.0 | Skip entry if Jupiter buy quote impact exceeds this |
| `MIN_LIQUIDITY_USD` | 12000 | Minimum pool liquidity for scanner and entry |
| `MOVE_SL_TO_BREAKEVEN_AFTER_L1` | true | Move stop to entry price on remaining 75% after L1 TP |
| `LADDER_EARLY_EXIT_LEVELS` | 2,3 | After these ladder levels, check momentum slowdown before next TP |
| `MOMENTUM_SLOWDOWN_PCT` | 0.5 | Exit remaining if recent momentum falls below 50% of peak |
| `INSTANT_PROFIT_EXIT_ENABLED` | true | Sell all remaining when unrealized profit hits instant threshold |
| `INSTANT_PROFIT_EXIT_PCT` | 0.05 | Minimum unrealized PnL (+5%) for instant full exit |
| `WEAKEN_EXIT_ENABLED` | true | Sell all remaining when trend weakens at â‰¥ min profit |
| `WEAKEN_EXIT_MIN_PROFIT_PCT` | 0.02 | Minimum unrealized PnL (+2%) for global weaken exit |
| `MAX_CONSECUTIVE_LOSSES` | 0 (disabled) | Pause new entries after this many losing trades in a row; `0` = no limit |
| `MAX_DAILY_LOSS_SOL` | 1.0 | Block new entries when daily net loss exceeds this |
| `AUTO_STOP_ON_MAX_DAILY_LOSS` | false | Stop the bot entirely when daily loss cap is hit |
| `FEE_BUFFER_SOL` | (auto) | Optional manual override for round-trip fee estimate |
| `TAKE_PROFIT_LEVELS` | 0.015,0.03,0.07,0.10 | Fixed ladder (+1.5%, +3%, +7%, +10%) for all trade sizes |
| `TAKE_PROFIT_PORTIONS` | 0.25,0.25,0.25,0.25 | Sell 25% of position at each TP level |
| `STOP_LOSS_PCT` | 0.015 | Sell all remaining at -1.50% loss (GUI dropdown also offers 3.0% and 5.0%) |
| `TIME_STOP_MINUTES` | 30 | Sell all remaining if ladder not fully hit in 30 min |
| `TRADE_SIZE_SOL` | 0.05 | SOL per trade â€” GUI dropdown: 0.05, 0.07, 0.10, 0.20, 0.30, 0.50, or 1.0 |
| `MAX_WALLET_TRADE_PCT` | 0.75 | Backend safety ceiling: max share of wallet SOL per automated trade (75%); not exposed in GUI |
| `MAX_OPEN_POSITIONS` | 2 | Max concurrent token positions (different mints) |
| `REENTRY_DIP_PCT` | 0.08 | Re-buy same mint after -8% drop from last exit price |
| `TRADE_COOLDOWN_CYCLES` | 5 | Poll cycles before re-entering same mint (bypassed on dip re-entry) |
| `MIN_FUND_SOL` | 0.75 | Minimum wallet balance required to start live trading |
| `PAPER_SIMULATED_BALANCE_SOL` | 0.75 | Starting simulated wallet balance for paper/dry-run; tracked live through the session (buys subtract, sells add). Bot auto-stops when depleted before 24h ends. |
| `PAPER_SESSION_HOURS` | 24 | Paper session duration; bot auto-stops when expired (`paper_stop_reason: session_expired`) |

While the bot is running, each entry uses your selected **Trade Size (SOL)**, further limited by `MAX_POSITION_SOL` and a backend safety cap on available wallet balance (`MAX_WALLET_TRADE_PCT`, default 75%):

```
trade_size = min(TRADE_SIZE_SOL, MAX_POSITION_SOL, (balance - MIN_SOL_RESERVE) Ã— MAX_WALLET_TRADE_PCT)
```

Example: 1.0 SOL wallet with 0.05 SOL selected â†’ 0.05 SOL per trade. In paper mode the same formula applies using the **running** simulated balance (starts at `PAPER_SIMULATED_BALANCE_SOL`, default 0.75 SOL) instead of the real RPC balance.

**Exit strategy (laddered take-profit):** On entry the bot buys the full trade size and uses a fixed 4-level ladder at +1.5%, +3%, +7%, +10% (25% of position sold at each level) regardless of trade size. **Exit priority:** (1) stop-loss / time-stop, (2) instant +5% full exit (`sell_instant_5pct`), (3) L2/L3 momentum slowdown, (4) global trend-weakening at â‰¥2% (`sell_trend_weaken_2pct`), (5) ladder partial TPs L1â€“L4. L1 (+1.5%) and L2 (+3%) partials fire first; instant +5% full exit fires after L1/L2 but before L3 (+7%) and L4 (+10%). Weak momentum at â‰¥2% can fire after L1 partial. After L1 fills, stop-loss on the remaining 75% moves to breakeven (entry price). At **+5%** unrealized profit the bot sells all remaining immediately. At **â‰¥2%** with weakening momentum, the same slowdown detection as L2/L3 triggers a full exit. Regular stop-loss at -1.5%, -3.0%, or -5.0% (selectable in the GUI) applies first; time-stop sells all remaining tokens when the hold limit is reached. Journal entries record `gross_pnl_sol`, `estimated_fees_sol`, and `net_pnl_sol`; running P&L uses net only.

After a profitable exit, new entries are ranked by similarity to the last winning trade profile (momentum, liquidity, volume, timeframe).

**Multi-position nonstop mode:** The bot monitors all open positions each poll and attempts new entries whenever open count is below `MAX_OPEN_POSITIONS` (default 2). After any close, the freed slot is eligible on the next poll â€” no extra delay beyond `PRICE_POLL_SEC` and normal cooldown rules.

**Dip re-entry:** When a mint is fully closed, its exit price is stored. If price later falls to `last_exit Ã— (1 - REENTRY_DIP_PCT)` (default -8%), the bot may re-enter that mint even if it is still on trade cooldown. Re-entry does not stack â€” you cannot hold two positions in the same mint.

## Project layout

```
C:\Users\Owner\Desktop\Solana\
  main.py           CLI entry point
  app.py            Web GUI server (Flask)
  gui.py            Web GUI entry alias
  launch.ps1        PowerShell launcher logic (used by Start Bot.bat)
  Start Bot.bat     One-click launcher â€” THE file to double-click
  install.bat       Create .venv + install dependencies
  stop.bat          Stop background Flask server
  run_hidden.vbs    Launch without console (advanced; server stays running)
  install_startup.bat  Auto-start at Windows login
  watchdog.py       Optional server watchdog
  bot_manager.py    Bot lifecycle wrapper for GUI
  bot.py            Main orchestration loop
  config.py         Environment configuration
  scanner.py        DexScreener mover scanner + unified merge
  pumpfun_scanner.py pump.fun token scanner (API + DexScreener fallback)
  birdeye_scanner.py Birdeye Find Gems scanner (1h gainers via meme-list API)
  gmgn_scanner.py   GMGN.ai Solana trending scanner (quotation rank API + DexScreener fallback)
  price_feed.py     Jupiter price polling + baselines
  strategy.py       Entry/exit logic
  reentry_tracker.py Per-mint exit price tracking for dip re-entry
  similarity.py     Post-exit mover ranking
  jupiter.py        Jupiter quote/swap execution
  solana_client.py  RPC client + keypair
  risk.py           Position sizing, daily loss cap, journal
  pnl_tracker.py    Running session P&L (paper + live)
  tax_export.py     Live-trade tax CSV log + monthly/yearly summaries
```

## Risks

- Memecoin slippage and priority fees can exceed small take-profit targets
- Gainers lists include scam/rug tokens; filters reduce but do not eliminate risk
- Polling latency means prices can move before your transaction lands
- This is experimental software, not financial advice

## Configuration reference

See `.env.example` for all tunable parameters.

### Best Win strategy

See **[BEST_WIN_STRATEGY.md](BEST_WIN_STRATEGY.md)** for the full rule set (entry, exits, fees, watchlist). Paper and live share `strategy.py` / `risk.py`; only the balance source differs.

**Apply from GUI:** Wallet & Strategy â†’ **Best Win** (auto-saves bookmark) â†’ **Apply Config** if needed.

**Revert:** **Revert to bookmark** or `POST /api/config/restore-bookmark` (snapshot at `presets/win_focused_bookmark.json`).

**Env template:** copy `.env.best_win.example` into `.env` or merge keys manually.

### Revert Best Win preset (config bookmark)

Before applying Best Win, the dashboard saves a revertible snapshot at **`presets/win_focused_bookmark.json`** (label `pre-best-win`). It captures whatever trading settings were active immediately before the preset was applied.

**Revert from the GUI:** Wallet & Strategy â†’ **Revert to bookmark** (calls `POST /api/config/restore-bookmark`).

**Revert from the shell** (updates runtime `Config` and `.env`):

```bat
restore_bookmark.bat
```

```powershell
.\restore_bookmark.ps1
```

Restart the Flask server after a shell restore if the dashboard was already running.

**Paper balance on Stop:** In paper mode, clicking **Stop Bot** resets the simulated SOL balance to your configured paper target (`target_balance_sol` in `paper_session_state.json`). Live mode is unchanged.

### Scanner API keys

Add keys to your `.env` file in the project root (`C:\Users\Owner\Desktop\Solana\.env`). The GUI shows **configured** / **missing** badges for each source (never the key values).

| Env variable | Required? | Where to get it | Notes |
|--------------|-----------|-----------------|-------|
| `DEXSCREENER_API_KEY` | No | [docs.dexscreener.com](https://docs.dexscreener.com/api/reference) | Official public API needs **no key** (60 req/min). Env var is optional for future/premium access; bot uses public endpoints if empty. |
| `PUMPFUN_API_KEY` | No | [pump.fun](https://pump.fun) wallet login â†’ JWT | Public `frontend-api.pump.fun` coin lists work without auth. Optional JWT (`Authorization: Bearer â€¦`) from wallet login may help on protected v3 routes. |
| `BIRDEYE_API_KEY` | **Recommended** | [birdeye.so](https://birdeye.so) â†’ Developer â†’ API Key | Free tier available. Sent as `X-API-KEY` with `x-chain: solana`. Primary endpoint: [`GET /defi/v2/tokens/new_listing`](https://docs.birdeye.so/reference/get-defi-v2-tokens-new_listing). Without it, Birdeye API is skipped and DexScreener trending fallback is used instead. |
| `GMGN_API_KEY` | No | [gmgn.ai/ai](https://gmgn.ai/ai?chain=sol&tab=skills_market) â†’ create Agent API key | Public quotation API (`/defi/quotation/v1/rank/sol/swaps/...`) works without a key. Optional key enables the official Agent API (`GET /v1/market/rank` via `gmgn-cli`). |

**Birdeye setup (one-time):**

1. Sign up at [birdeye.so](https://birdeye.so)
2. Open **Developer â†’ API Key** and create a free key
3. Paste into `.env`: `BIRDEYE_API_KEY=your_key_here`
4. **Restart the server** (stop and start the bot / Flask app) so the key loads

Example `.env` snippet:

```env
DEXSCREENER_API_KEY=
PUMPFUN_API_KEY=
BIRDEYE_API_KEY=your_birdeye_key_here
```

If a key is missing, the bot logs once at WARNING (Birdeye) or INFO (other sources) and continues with public/fallback endpoints where possible.

### Pump.fun scanner

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SCAN_PUMPFUN` | true | Include pump.fun tokens in the watchlist (alias: `INCLUDE_PUMPFUN`) |
| `PUMPFUN_API_KEY` | (empty) | Optional JWT from pump.fun wallet login â€” improves access to protected API routes |
| `PUMPFUN_MIN_MARKET_CAP_USD` | 5000 | Minimum market cap for pump.fun candidates |
| `PUMPFUN_MAX_AGE_MINUTES` | 0 | Max pool age in minutes (0 = disabled; use e.g. 120 for fresh launches only) |
| `PUMPFUN_MIN_LIQUIDITY_USD` | (same as `MIN_LIQUIDITY_USD`) | Optional lower liquidity threshold for pump.fun |
| `PUMPFUN_MIN_VOLUME_24H_USD` | (same as `MIN_VOLUME_24H_USD`) | Optional volume override for pump.fun |
| `PUMPFUN_MIN_MOMENTUM_PCT` | (same as `MIN_MOMENTUM_PCT`) | Optional momentum override for pump.fun |

Trades execute via **Jupiter** when a route exists (graduated or liquid curve tokens). Tokens with no Jupiter route are skipped automatically.

### Birdeye Find Gems scanner

1h % gainers and newly listed tokens from [Birdeye Find Gems](https://birdeye.so/solana/find-gems) are fetched via the Birdeye public API and merged into the unified watchlist alongside DexScreener and pump.fun. Each tokenâ€™s contract address (CA/mint) is taken from the API response.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SCAN_BIRDEYE` | true | Include Birdeye Find Gems in the watchlist |
| `BIRDEYE_FIND_GEMS_ENABLED` | true | Use Find Gems 1h gainers as the primary Birdeye source |
| `BIRDEYE_GAINER_TIMEFRAME` | 1h | Gainer sort window (`1h`, `24h`, `5m`, etc.) â€” maps to Birdeye `sort_by` |
| `BIRDEYE_API_KEY` | (empty) | API key from [birdeye.so](https://birdeye.so) â€” **required** for Find Gems; without it DexScreener trending fallback is used |
| `BIRDEYE_MIN_LIQUIDITY_USD` | (same as `MIN_LIQUIDITY_USD`) | Optional liquidity threshold override for Birdeye candidates |
| `BIRDEYE_MIN_VOLUME_24H_USD` | (same as `MIN_VOLUME_24H_USD`) | Optional volume override for Birdeye candidates |

**API setup:** Sign up at [birdeye.so](https://birdeye.so), create an API key under Developer â†’ API Key, set `BIRDEYE_API_KEY=your_key` in `.env`, and restart the server. With a key, the scanner uses [`GET /defi/v3/token/meme/list`](https://docs.birdeye.so/reference/get-defi-v3-token-meme-list) sorted by `price_change_1h_percent` (Find Gems 1h gainers), with fallbacks to [`GET /defi/v2/tokens/new_listing`](https://docs.birdeye.so/reference/get-defi-v2-tokens-new_listing) (`meme_platform_enabled=true`), `GET /defi/token_trending`, and `GET /defi/v3/token/list`. Candidates are enriched via DexScreener pair data for liquidity/momentum filters. Without a key, Birdeye endpoints are not called (no 401 spam); DexScreener boosted/trending pairs are used as fallback instead.

### GMGN.ai scanner

Solana trending tokens from [GMGN.ai](https://gmgn.ai/?chain=sol) are fetched via the public **quotation rank API** and merged into the unified watchlist. The [skills market tab](https://gmgn.ai/ai?chain=sol&tab=skills_market) is GMGN's Agent API key portal (for `gmgn-cli` / MCP skills like `/gmgn-market`); it does **not** return token lists directly. Token contract addresses (CAs) come from rank responses (`address` field).

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SCAN_GMGN` / `GMGN_ENABLED` | true | Include GMGN trending tokens in the watchlist |
| `GMGN_API_KEY` | (empty) | Optional Agent API key from [gmgn.ai/ai](https://gmgn.ai/ai) â€” public quotation API works without it |
| `GMGN_TIMEFRAME` | 1h | Rank window (`5m`, `1h`, `6h`, `24h`, etc.) |
| `GMGN_TRENDING_LIMIT` | 30 | Max tokens fetched per rank query (40 with max potential) |
| `GMGN_REQUEST_DELAY_SEC` | 1.0 | Pace between GMGN HTTP requests |
| `GMGN_SAFETY_FILTERS` | `not_honeypot` | Comma-separated GMGN `filters[]` values (e.g. `not_honeypot,verified`) |
| `GMGN_MIN_LIQUIDITY_USD` | (same as `MIN_LIQUIDITY_USD`) | Optional liquidity override for GMGN candidates |
| `GMGN_MIN_VOLUME_24H_USD` | (same as `MIN_VOLUME_24H_USD`) | Optional volume override for GMGN candidates |

**Endpoints used (mint in `address`):**

- `GET https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/{timeframe}?orderby=volume&direction=desc&limit=N&filters[]=not_honeypot`
- `GET https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/{timeframe}?orderby=swaps|price_change|smartmoney&...`
- `GET https://gmgn.ai/defi/quotation/v1/rank/sol/pump/{timeframe}?orderby=volume&...` (near-completion pump.fun tokens)

Candidates missing liquidity/momentum fields are enriched via DexScreener pair lookup (same fallback pattern as Birdeye).

### Pinned watchlist mint

Track a single mint every scan cycle (even if it is not a DexScreener mover) and allow entry when its USD price has risen by at least the configured threshold from the **rolling baseline** used for momentum (`BASELINE_WINDOW_SEC`, default 30s).

| Parameter | Default | Description |
|-----------|---------|-------------|
| `WATCHLIST_ENABLED` | true | Enable pinned-mint watchlist trading |
| `WATCHLIST_MINT` | `3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh` | Solana mint to monitor |
| `WATCHLIST_MIN_USD_GAIN` | 75.0 | Minimum **absolute USD price increase** from rolling baseline to qualify (`current_usd - baseline_usd >= 75`) |

When qualified, the token is prepended to the watchlist with source `watchlist_mint`. Entry uses the USD gain trigger instead of `ENTRY_MOMENTUM_PCT`; exits use the same ladder (+1.5% / +3% / +7% / +10%), instant +5%, stop-loss, weaken exit, and time-stop rules as all other trades. The dashboard **Scan Sources** panel shows live gain (e.g. `Watchlist: SYM +$82.50 (trade eligible)`).

### DexScreener scanner

DexScreener movers use the public REST API at `https://api.dexscreener.com` (no official API key program). Optional `DEXSCREENER_API_KEY` is supported for compatibility if DexScreener adds premium auth later.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DEXSCREENER_API_KEY` | (empty) | Optional â€” not required for the public API |
| `DEXSCREENER_MAX_SEED_MINTS` | 50 | Max boosted/profile seed mints resolved per scan (75 with `MAX_POTENTIAL_MODE`) |
| `WATCHLIST_TOP_N` | 40 | Top movers kept on the ranked watchlist (50 with max potential) |

DexScreener discovery uses token boosts (top + latest), latest profiles, and multi-query search (`SOL/USDC`, `USDC/SOL`, `SOL`, `trending`).

### Max Potential mode

Set `MAX_POTENTIAL_MODE=true` in `.env` for the widest token discovery while keeping profit-first exits intact (ladder, instant +5%, stop-loss unchanged).

| Setting | Standard default | Max potential |
|---------|------------------|---------------|
| `SCAN_INTERVAL_SEC` | 5 | 5 (do not go below 5s) |
| `DEXSCREENER_MAX_SEED_MINTS` | 50 | 75 |
| `WATCHLIST_TOP_N` | 40 | 50 |
| `BIRDEYE_TRENDING_LIMIT` | 30 | 40 (+ trending / meme fallbacks) |
| `PUMPFUN_API_LIMIT` | 50 | 100 (+ featured, for-you, graduated feeds) |
| `MIN_LIQUIDITY_USD` effective | $12,000 | min($12k, $10k) |
| `MIN_VOLUME_24H_USD` effective | $50,000 | min($50k, $35k) |
| `MAX_ENTRY_PRICE_IMPACT_PCT` effective | 1.0% | max(1.0%, 1.25%) |

The GUI shows a **Max Potential** badge in the header when enabled, per-source mover counts (DexScreener / pump.fun / Birdeye), and **configured** / **missing** API key badges.

Recommended `.env` block for max potential:

```env
MAX_POTENTIAL_MODE=true
SCAN_PUMPFUN=true
SCAN_BIRDEYE=true
SCAN_INTERVAL_SEC=5
DEXSCREENER_MAX_SEED_MINTS=75
WATCHLIST_TOP_N=50
BIRDEYE_TRENDING_LIMIT=40
PUMPFUN_API_LIMIT=100
BIRDEYE_API_KEY=your_key_here
MIN_LIQUIDITY_USD=10000
MIN_VOLUME_24H_USD=35000
MIN_MOMENTUM_PCT=0.008
MAX_ENTRY_PRICE_IMPACT_PCT=1.25
```
