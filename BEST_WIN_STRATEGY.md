# Best Win Trading Strategy



Cohesive preset for **paper and live** trading. Both modes use the same `strategy.py`, `risk.py`, and `fee_estimator.py` paths — only the balance source differs (simulated vs wallet).



## Quick apply



1. Open the dashboard → **Wallet & Strategy**

2. Click **Apply best win strategy** (saves `presets/best_win_bookmark.json` automatically)

3. Click **Apply Config** if you changed RPC or other fields

4. Start bot in paper or live mode



**Revert:** **Revert to bookmark** restores `presets/best_win_bookmark.json` (or legacy `presets/win_focused_bookmark.json`).



**Env auto-apply:** set `BEST_WIN_STRATEGY=true` in `.env` to apply on server start (saves bookmark first).



---



## Entry rules



| Rule | Value | Notes |

|------|-------|-------|

| Trade size | **0.10 SOL** | Fee-viable; ladder L1 net clears ~0.002 SOL after chain + DEX fees |

| Entry momentum | **0.75%** | `ENTRY_MOMENTUM_PCT=0.0075` |

| Min 24h volume (non-watchlist) | **$40,000** | `NON_WATCHLIST_MIN_VOLUME_24H_USD` / preset `min_volume_24h_usd` |

| Min pool liquidity | **$15,000** | Discovery floor; when `MAX_POTENTIAL_MODE` off |

| GMGN min liquidity | **$20,000** | GMGN-sourced candidates only (`source=gmgn`) |

| Scanner momentum | **1.5%** | `MIN_MOMENTUM_PCT=0.015` |

| Profit-first entry | **≥ 0.002 SOL** net | Expected ladder net after fees must clear `MIN_EXPECTED_NET_PROFIT_SOL` |

| Stock tokens | **Blocked** | SPCX, xStocks, and related symbols filtered |

| Candidate pool | **Top 10** | Unified scan: DexScreener, pump.fun, Birdeye, GMGN |

| Jupiter route | **Required** | No route → skip |

| Re-entry dip | **10%** | From last exit price on same mint (`REENTRY_DIP_PCT`) |

| Loss cooldown | **90 min** | Same mint blocked after stop-loss (`LOSS_REENTRY_COOLDOWN_MINUTES`) |

| Repeat loss cooldown | **180 min** | 2nd+ loss on same mint in session (`LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES`) |



### Watchlist (pinned mints)



| Mint | Label | Entry | Exit |

|------|-------|-------|------|

| `3NZ9JMVB…` | WBTC | **$75 day USD gain** | Standard ladder + exits; **profit-only** voluntary sells (see below) |

| `6M8z5Wzm…` | 6M8z | **5% day gain** | Hold to **+20%** (one buy / one sell) |



---



## Position limits



| Condition | Max open positions |

|-----------|-------------------|

| Default | **1** |

| WBTC held or next entry while 1 other open | **2** |



---



## Exit rules



| Exit type | Trigger | Action |

|-----------|---------|--------|

| Ladder L1 | **+3%** gross | Sell **50%** (if est. net ≥ `MIN_NET_WIN_SOL`) |

| Ladder L2 | **+4%** gross | Sell **50%** of remainder |

| L1 protection | PnL ≤ **+0.10%** after L1 partial | Sell remainder (not a stop-loss) |

| Instant profit | **+5%** gross | Full exit |

| Min net win gate | **0.002–0.003 SOL** | Blocks voluntary exits below threshold |

| **WBTC profit-only exits** | `WBTC_PROFIT_ONLY_EXITS=true` (default) | WBTC voluntary sells require quote-verified net ≥ `MIN_NET_WIN_SOL`; stop-loss and L1 protection still fire at a loss |

| Stop loss (memecoins) | **1.5%** | Full exit |

| Stop loss (WBTC only) | **2%** | Full exit via `WBTC_STOP_LOSS_PCT` |

| Weaken exit | **+1%** gross + min net check | Trend weaken sell |

| Ladder time — positive | **10 min** | Exit if ladder missed while green |

| Ladder time — negative | **30 min** | DCA or exit per ladder timeout rules (WBTC exempt) |

| 6M8z watchlist | **+20%** | Full exit (overrides ladder) |



---



## Fee model (paper = live)



- **Chain:** `SOL_TX_FEE_LAMPORTS` (5000) + `SOL_PRIORITY_FEE_LAMPORTS` (100000) per signature

- **DEX:** Jupiter route labels → per-DEX bps; fallback `DEFAULT_DEX_FEE_BPS=25`

- **Buffer:** `FEE_BUFFER_PCT=0.10` on subtotal

- **Paper:** Same deductions via `fee_estimator.py` in exit gates and PnL preview



### WBTC fee thresholds (0.10 SOL trade size)



| Gross move | Net after ~0.0031 SOL round-trip | Action |

|------------|----------------------------------|--------|

| +1.5% | **Loss** | Never sell (voluntary) |

| ~+3.1% | Breakeven full exit | Minimum to avoid net loss |

| +3% / +4% ladder | Needs quote check | L1/L2 only if net ≥ `MIN_NET_WIN_SOL` (0.002–0.003) |

| +5% instant | Needs quote check | Full exit only if net profitable after fees |



**Risk exits (always allowed for WBTC):** stop-loss at **−2%** (`WBTC_STOP_LOSS_PCT`); L1 protection floor at **+0.10%** after L1 partial (loss prevention, not profit-taking).



---



## Preset keys (`BEST_WIN_STRATEGY_PRESET`)



Extends `BEST_WIN_PRESET` with strict scanner posture:



```python

# Core (BEST_WIN_PRESET)

trade_size_sol: 0.10

entry_momentum_pct: 0.0075

stop_loss_pct: 0.015          # memecoins; WBTC uses 2% override

min_liquidity_usd: 15000

min_volume_24h_usd: 40000       # non-watchlist discovery floor

min_momentum_pct: 0.020

min_expected_net_profit_sol: 0.003

min_net_win_sol: 0.003

max_entry_price_impact_pct: 0.75

loss_reentry_cooldown_minutes: 90

loss_reentry_repeat_cooldown_minutes: 180

reentry_min_momentum_pct: 0.005

weaken_exit_min_profit_pct: 0.01

take_profit_levels: [0.03, 0.04]

take_profit_portions: [0.5, 0.5]

# Strategy extras

reentry_dip_pct: 0.10

max_potential_mode: false   # strict $15k / 1.5% momentum (no relaxed discovery)

block_stock_related_tokens: true

gmgn_min_liquidity_floor_usd: 20000

```

Copy env template: `.env.best_win.example`



---



## Session learnings (Jul 2026 — 14 exits, net −0.0486 SOL)

Analysis: full replay of `trades.jsonl` paper session (CSV export unavailable at user path).

| Metric | Value |
|--------|-------|
| Closed trades | 14 |
| Win rate | 14.3% (2 W / 12 L) |
| Avg win / loss | +0.0045 / −0.0048 SOL |
| Net PnL | −0.0486 SOL |

| Exit reason | Count | Net PnL |
|-------------|-------|---------|
| Stop loss | 10 | −0.0568 SOL |
| Trend weaken | 2 | +0.0091 SOL |
| Ladder timeout 30m (WBTC) | 2 | −0.0009 SOL |

| Finding | Change applied |
|---------|----------------|
| 10/12 losses were **stop-loss** on memecoins | **2.0%** scanner momentum, **0.75%** entry poll, **0.75%** max impact, **1.5%** memecoin SL |
| Repeat losers (TESTIBULL 3×, manlet 2×, NMO 2×) | **90 min** cooldown; **180 min** on 2nd+ loss; dip re-entry needs **0.5% momentum** |
| Dip re-entries at **0% momentum** after wins/losses | `REENTRY_MIN_MOMENTUM_PCT=0.005` gate in `evaluate_dip_reentry` |
| High slippage entries (TESTIBULL 6.3%, PATTYICE 3.7%) | `MAX_ENTRY_PRICE_IMPACT_PCT=0.75` |
| Wins only from **weaken exit** (+3–4%) | Kept weaken at +1%; fewer bad entries improves hit rate |
| WBTC small **30m negative** exits | WBTC **defers** forced 30m negative exit (hold for ladder/SL) |
| Thin GMGN pools | **$20k** GMGN liquidity floor via `effective_gmgn_min_liquidity()` |
| Fee-only wins | None observed; `min_net_win_sol` raised to **0.003** as buffer |

**Note:** Session ran with stale `.env` (45m cooldown, 2% SL, 0.5% entry). Re-apply **Best Win** preset before next run.



---



## Tight losses variant (optional)



Hidden GUI preset — same as Best Win but `min_net_win_sol=0.003` and `loss_reentry_cooldown_minutes=120`.

