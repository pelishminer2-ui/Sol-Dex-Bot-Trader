"""Generate Sol-Dex-Bot-Trader-User-Guide.pdf (regenerable).

Writes:
  - docs/Sol-Dex-Bot-Trader-User-Guide.pdf
  - setup-bot-installer/Sol-Dex-Bot-Trader-User-Guide.pdf
  - setup-bot-installer/output/Sol-Dex-Bot-Trader-User-Guide.pdf
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = Path(__file__).resolve().parent
ASSETS = INSTALLER / "assets"
DOCS = ROOT / "docs"
OUT_DOCS = DOCS / "Sol-Dex-Bot-Trader-User-Guide.pdf"
OUT_INSTALLER = INSTALLER / "Sol-Dex-Bot-Trader-User-Guide.pdf"
OUT_OUTPUT = INSTALLER / "output" / "Sol-Dex-Bot-Trader-User-Guide.pdf"

FEE_WALLET = "8TdLLnveaK5iFD6dmVU7qfw8V14cM7CyCcHiZfgcRQMi"
FEE_SOL = "0.025"
INSTALL_DIR = r"%LOCALAPPDATA%\Programs\Sol Dex Bot Trader\\"
DASHBOARD_URL = "http://127.0.0.1:5000"
VERSION_FILE = Path(__file__).resolve().parent / "version.txt"


def _app_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "1.1.7"
    except OSError:
        return "1.1.7"



def _guide_built_stamp() -> str:
    """Local wall-clock stamp embedded on the PDF cover (rebuild refreshes this)."""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

BRAND = colors.HexColor("#0f2744")
ACCENT = colors.HexColor("#1a6b8a")
DANGER = colors.HexColor("#8b1e1e")
WARN_BG = colors.HexColor("#fff3cd")
DANGER_BG = colors.HexColor("#f8d7da")
OK_BG = colors.HexColor("#eaf6ea")
OK_BORDER = colors.HexColor("#2d6a3e")
MUTED = colors.HexColor("#555555")


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "TitleCustom",
            parent=base["Title"],
            fontSize=22,
            textColor=BRAND,
            spaceAfter=8,
            alignment=TA_CENTER,
        ),
        "subtitle": ParagraphStyle(
            "SubtitleCustom",
            parent=base["Normal"],
            fontSize=11,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=16,
        ),
        "h1": ParagraphStyle(
            "H1Custom",
            parent=base["Heading1"],
            fontSize=14,
            textColor=BRAND,
            spaceBefore=14,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "H2Custom",
            parent=base["Heading2"],
            fontSize=12,
            textColor=ACCENT,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "BodyCustom",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            alignment=TA_JUSTIFY,
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "BulletCustom",
            parent=base["Normal"],
            fontSize=10,
            leading=13,
            leftIndent=8,
        ),
        "warn": ParagraphStyle(
            "WarnCustom",
            parent=base["Normal"],
            fontSize=10,
            leading=13,
            textColor=DANGER,
            alignment=TA_LEFT,
        ),
        "ok": ParagraphStyle(
            "OkCustom",
            parent=base["Normal"],
            fontSize=10,
            leading=13,
            textColor=OK_BORDER,
            alignment=TA_LEFT,
        ),
        "caption": ParagraphStyle(
            "CaptionCustom",
            parent=base["Normal"],
            fontSize=8,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=10,
            spaceBefore=2,
        ),
        "footer": ParagraphStyle(
            "FooterCustom",
            parent=base["Normal"],
            fontSize=8,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
    }
    return styles


def _callout(text: str, styles, bg=DANGER_BG, border=DANGER, style_key="warn") -> KeepTogether:
    p = Paragraph(text, styles[style_key])
    t = Table([[p]], colWidths=[6.5 * inch])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("BOX", (0, 0), (-1, -1), 1.5, border),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return KeepTogether([t, Spacer(1, 10)])


def _img(name: str, width: float = 6.2 * inch) -> list:
    path = ASSETS / name
    if not path.exists():
        return [Paragraph(f"<i>[Image missing: {name}]</i>", _styles()["caption"])]
    im = Image(str(path))
    aspect = im.imageHeight / float(im.imageWidth)
    im.drawWidth = width
    im.drawHeight = min(width * aspect, 4.2 * inch)
    if width * aspect > 4.2 * inch:
        im.drawWidth = 4.2 * inch / aspect
        im.drawHeight = 4.2 * inch
    return [im]


def build_story(styles) -> list:
    story: list = []

    story.append(Paragraph("Sol Dex Bot Trader", styles["title"]))
    story.append(Paragraph("End-User Guide — Install, Run, Wallet, Operate, Safety", styles["subtitle"]))
    story.append(
        Paragraph(
            f"Version {_app_version()}  ·  Guide built {_guide_built_stamp()}",
            styles["subtitle"],
        )
    )

    story.append(
        _callout(
            "<b>RISK DISCLAIMER — READ FIRST:</b> Cryptocurrency trading is highly speculative. "
            "<b>Gains are not guaranteed.</b> You can lose some or all of the funds you allocate. "
            "Past paper or live results do not predict future performance. "
            "<b>You invest and trade at your own risk.</b> "
            "This software is a local trading assistant only — not financial advice.",
            styles,
            bg=DANGER_BG,
            border=DANGER,
        )
    )

    # --- 1. Install ---
    story.append(Paragraph("1. Install &amp; first launch (setup.exe)", styles["h1"]))
    story.append(
        Paragraph(
            "Run <b>setup.exe</b>. Accept the prompts and finish the wizard. "
            "You do <b>not</b> need PowerShell scripts or <font face='Courier'>.bat</font> files. "
            "On the Finish page, an optional <b>Launch Sol Dex Bot Trader</b> checkbox appears "
            "(off by default) — check it only if you want to start the app immediately. "
            "Otherwise start from the Start Menu or Desktop shortcut when ready. "
            "Maintainer note: rebuilding with <font face='Courier'>build.bat</font> / "
            "<font face='Courier'>build.ps1</font> does <b>not</b> auto-start the app after the build finishes. "
            "On launch, the app starts the local server and opens your browser to "
            f"<font face='Courier'>{DASHBOARD_URL}</font>.",
            styles["body"],
        )
    )
    story.append(
        _callout(
            "<b>Browser for Connect:</b> Open the dashboard in <b>Chrome</b>, <b>Edge</b>, or <b>Brave</b> "
            "with the <b>Phantom</b> or <b>Solflare</b> extension installed. "
            "Wallet extensions do <b>not</b> work in some embedded / in-app browsers "
            "(the Connect approval popup will not appear).",
            styles,
            bg=WARN_BG,
            border=colors.HexColor("#856404"),
        )
    )
    story.extend(_img("install-flow.png", width=6.3 * inch))
    story.append(
        Paragraph(
            "Install flow: setup.exe → optional Finish Launch checkbox → "
            "background app (tray) → browser dashboard (or start later from Start Menu / Desktop).",
            styles["caption"],
        )
    )
    story.append(
        ListFlowable(
            [
                ListItem(
                    Paragraph(
                        f"<b>Install folder:</b> <font face='Courier'>{INSTALL_DIR}</font> "
                        "(Windows per-user Programs path)",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        f"<b>.env created</b> under that folder on first launch "
                        f"(seeded from <font face='Courier'>.env.example</font> if missing). "
                        f"Full path example: <font face='Courier'>{INSTALL_DIR}.env</font>",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "Start Menu and optional Desktop shortcut: <b>Sol Dex Bot Trader</b> "
                        "(or check the optional Finish-page Launch checkbox). "
                        "Desktop shortcut uses the <b>Pelish Crypto</b> medallion icon; "
                        "the app / taskbar / tray use the <b>Cats of Crypto</b> icon.",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "User Guide PDF installed under the app <font face='Courier'>docs</font> folder "
                        "(also linked in Start Menu)",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "Dashboard loads on localhost only — never expose port 5000 to the internet",
                        styles["bullet"],
                    )
                ),
            ],
            bulletType="bullet",
            start="•",
        )
    )
    story.append(Spacer(1, 6))

    # --- 2. Runs without a console window ---
    story.append(Paragraph("2. Runs without a CMD / console window", styles["h1"]))
    story.append(
        _callout(
            "<b>No black CMD window:</b> The packaged app runs as a <b>windowed background process</b> "
            "(no DOS/CMD console that must stay open). "
            "A <b>system tray icon</b> appears while the server is running. "
            "Your browser opens to "
            f"<font face='Courier'>{DASHBOARD_URL}</font>. "
            "Diagnostics go to "
            f"<font face='Courier'>{INSTALL_DIR}logs\\soldexbot.log</font>.",
            styles,
            bg=OK_BG,
            border=OK_BORDER,
            style_key="ok",
        )
    )
    story.append(
        Paragraph(
            "How to tell it is running and how to stop it:",
            styles["body"],
        )
    )
    story.append(
        ListFlowable(
            [
                ListItem(
                    Paragraph(
                        "<b>Running:</b> tray icon present + dashboard loads at "
                        f"<font face='Courier'>{DASHBOARD_URL}</font>",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Stop (preferred):</b> right-click the tray icon → <b>Quit</b>",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Stop (Start Menu):</b> <b>Stop Sol Dex Bot Trader</b> "
                        "(runs <font face='Courier'>Stop-SolDexBot.bat</font>)",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Stop (fallback):</b> "
                        "<font face='Courier'>taskkill /IM SolDexBotTrader.exe /F</font>",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Note:</b> Dashboard <b>Stop</b> only stops the trading loop — "
                        "it does <b>not</b> exit the local server. Use tray Quit (or Stop shortcut) "
                        "to fully shut down the app.",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Logs:</b> if something fails, open "
                        f"<font face='Courier'>{INSTALL_DIR}logs\\soldexbot.log</font> "
                        "(tray menu also has <b>Open Logs Folder</b>)",
                        styles["bullet"],
                    )
                ),
            ],
            bulletType="bullet",
            start="•",
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        _callout(
            "<b>Remember:</b> Closing the browser tab does <b>not</b> stop the bot. "
            "Use tray <b>Quit</b> or Start Menu <b>Stop Sol Dex Bot Trader</b>. "
            "Relaunch from Start Menu / Desktop when you want the dashboard again.",
            styles,
            bg=WARN_BG,
            border=colors.HexColor("#856404"),
        )
    )
    story.append(Paragraph("Long-run reliability &amp; auto-resume", styles["h2"]))
    story.append(
        Paragraph(
            "The app is designed to run continuously until you Quit. "
            "Paper sessions default to <b>continuous</b> (no 24h auto-stop; optional "
            "<font face='Courier'>PAPER_SESSION_HOURS</font> timed window). "
            "Open paper and live trades are saved to disk on every open/update/close. "
            "If Flask or the process restarts, open books are reloaded and exit monitoring "
            "continues (stop-loss, instant profit, and 15-minute rules are unchanged). "
            "Live signing uses the in-memory Set Wallet key (ephemeral) or "
            "<font face='Courier'>SOLANA_PRIVATE_KEY</font> in <font face='Courier'>.env</font> — "
            "Stop Bot clears the dashboard key field and the ephemeral session key from memory "
            "(re-Set Wallet before the next Live start if you do not use .env). "
            "Session RPC pasted in the dashboard is also cleared on Stop. "
            "Windows keep-awake and a Flask supervisor/watchdog help prevent sleep and silent exits.",
            styles["body"],
        )
    )

    story.append(PageBreak())

    # --- 3. Wallets ---
    story.append(Paragraph("3. Supported wallets (Phantom or Solflare ONLY)", styles["h1"]))
    story.append(
        _callout(
            "<b>Wallets:</b> Use <b>Phantom</b> or <b>Solflare</b> only. "
            "Do not use other wallets, hardware-export workflows this guide does not cover, "
            "or paste keys into untrusted websites.",
            styles,
            bg=WARN_BG,
            border=colors.HexColor("#856404"),
        )
    )
    story.append(
        Paragraph(
            "<b>Live trading requires two steps</b> — Connect (extension) then Set Wallet (base58 key). "
            "Paper mode does not require a key.",
            styles["body"],
        )
    )
    story.append(
        ListFlowable(
            [
                ListItem(
                    Paragraph(
                        "<b>1. Connect (upper right):</b> Click <b>Connect</b>. "
                        "Approve in the <b>Phantom</b> or <b>Solflare</b> extension popup "
                        "(the approval window must appear). "
                        "Use <b>Chrome / Edge / Brave</b> with the extension installed — "
                        "embedded browsers often block extension popups.",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>2. Set Wallet (base58):</b> Export your wallet’s <b>base58 private key</b> "
                        "(or JSON byte array) from Phantom/Solflare, paste it into the Wallet Key field, "
                        "and click <b>Set Wallet</b>. This is <b>required</b> for server auto-sign.",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>3. Auto-sign after Set Wallet:</b> Live start fee and Jupiter swaps "
                        "are signed on the server — <b>no extension popup per trade</b>.",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>4. Session key until Stop:</b> The key stays in the Wallet Key field and "
                        "server memory across Paper Trade toggle and status polls. "
                        "<b>Stop Bot</b> clears both the RPC and private-key fields and wipes the "
                        "ephemeral session key (and session RPC override) from memory. "
                        "You can set or update the key even while the bot is running.",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Security:</b> Never share your private key with anyone. "
                        "Prefer keeping it in memory only; use Save to .env only on a trusted personal PC.",
                        styles["bullet"],
                    )
                ),
            ],
            bulletType="bullet",
            start="•",
        )
    )

    story.append(Paragraph("4. Export private key — Phantom", styles["h1"]))
    story.extend(_img("phantom-export-key.png"))
    story.append(
        Paragraph(
            "Illustrative diagram (not a live screenshot). Typical path: Phantom → Settings → "
            "Security &amp; Privacy → Export Private Key → authenticate → copy the base58 key into the bot’s Wallet Key field → Set Wallet.",
            styles["caption"],
        )
    )

    story.append(Paragraph("5. Export private key — Solflare", styles["h1"]))
    story.extend(_img("solflare-export-key.png"))
    story.append(
        Paragraph(
            "Illustrative diagram. Typical path: Solflare → Settings → Export Security / Private Key → "
            "authenticate → copy base58 into the bot → Set Wallet. Prefer keeping the key in memory only; "
            "use Save to .env only on a trusted personal PC.",
            styles["caption"],
        )
    )

    story.append(PageBreak())

    # --- 6. Operate ---
    story.append(Paragraph("6. Operate the bot", styles["h1"]))
    story.extend(_img("bot-dashboard-overview.png"))
    story.append(
        Paragraph(
            "Illustrative dashboard overview. Your installed UI may differ slightly in layout.",
            styles["caption"],
        )
    )
    story.append(
        ListFlowable(
            [
                ListItem(
                    Paragraph(
                        "<b>Start Bot / Stop</b> — start or stop the trading loop "
                        "(server stays running in the tray until you Quit)",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Paper Trade</b> (recommended first) — simulated balance (default / start gate "
                        "<b>2.00 SOL</b>; dropdown 0.75–5.00), real market data, "
                        "no on-chain swaps, <b>no live-start fee</b>",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Paper quote currency</b> — choose <b>USDC</b>, <b>USDT</b>, or <b>Solana</b> "
                        "next to Paper Balance. The simulated wallet stays SOL-equivalent; USDC/USDT "
                        "only change labels and conversion using live SOL/USD. Default is Solana.",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Trade SOL/WSOL when daily +$5</b> — when quote is USDC or USDT, an optional "
                        "checkbox appears. Enable it to allow WSOL "
                        "(mint <font face='Courier'>So11111111111111111111111111111111111111112</font>) "
                        "entries when SOL’s DexScreener 24h day gain is at least <b>+$5</b> absolute USD. "
                        "Does <b>not</b> weaken stop-loss, profit exits, or forced sells.",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Live</b> — real Jupiter swaps with your funded wallet "
                        f"(<b>{FEE_SOL} SOL</b> fee applies on each Live start — see section 8)",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Open Trades</b> — view open positions; use <b>Sell</b> / Manual Sell for a full exit",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Actions / Smart Re-entry</b> — when a re-entry decision is pending, "
                        "use <b>Allow</b> or <b>Deny</b> in the Actions panel",
                        styles["bullet"],
                    )
                ),
            ],
            bulletType="bullet",
            start="•",
        )
    )

    story.append(Paragraph("7. Strategies — which button to use", styles["h1"]))
    story.extend(_img("strategy-buttons.png", width=6.3 * inch))
    story.append(
        Paragraph(
            "Strategy button row: Best Win · Steady Trade (recommended) · Balanced Win · Revert · Reset · Apply Config.",
            styles["caption"],
        )
    )
    rows = [
        [Paragraph("<b>Button</b>", styles["bullet"]), Paragraph("<b>What it does</b>", styles["bullet"])],
        [
            Paragraph("Best Win", styles["bullet"]),
            Paragraph("Strict filters — fewer, higher-quality entries", styles["bullet"]),
        ],
        [
            Paragraph("Steady Trade", styles["bullet"]),
            Paragraph("<b>Default / recommended</b> — balanced pace with loss protections", styles["bullet"]),
        ],
        [
            Paragraph("Balanced Win", styles["bullet"]),
            Paragraph("More trades, still fee-aware", styles["bullet"]),
        ],
        [
            Paragraph("Revert to bookmark", styles["bullet"]),
            Paragraph("Restore the saved pre-strategy bookmark config", styles["bullet"]),
        ],
        [
            Paragraph("Reset Defaults", styles["bullet"]),
            Paragraph("Reset spread / setup defaults", styles["bullet"]),
        ],
        [
            Paragraph("Apply Config", styles["bullet"]),
            Paragraph("Apply the values currently shown in the Setup panel", styles["bullet"]),
        ],
    ]
    table = Table(rows, colWidths=[1.6 * inch, 4.9 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#eaf6ea")),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 10))

    # --- 8. Fee ---
    story.append(Paragraph("8. Live trading fee (0.025 SOL)", styles["h1"]))
    story.append(
        _callout(
            f"<b>Live-start fee:</b> <b>{FEE_SOL} SOL</b> is charged <b>each time you start Live trading</b> "
            f"(not per trade, not in Paper mode). Payment uses a temporary / rented relay wallet: "
            f"your wallet → ephemeral relay → project fee wallet "
            f"<font face='Courier'>{FEE_WALLET}</font>. "
            f"If the fee payment fails, Live start is blocked. "
            f"<b>Gains are not guaranteed. You invest at your own risk.</b>",
            styles,
        )
    )

    # --- 9. Safety ---
    story.append(Paragraph("9. Safety", styles["h1"]))
    story.append(
        ListFlowable(
            [
                ListItem(
                    Paragraph(
                        "No CMD window is required — stop fully via tray <b>Quit</b> or "
                        "Start Menu <b>Stop Sol Dex Bot Trader</b>",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "Runs on <b>localhost only</b> (127.0.0.1) — do not port-forward or expose publicly",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Never share</b> your private key, seed phrase, or .env file",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "Start with <b>Paper Trade</b> until you understand Start/Stop, strategies, and exits",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "Keep only funds you can afford to lose in the trading wallet",
                        styles["bullet"],
                    )
                ),
                ListItem(
                    Paragraph(
                        "<b>Gains are not guaranteed</b> — markets move against you; you invest at your own risk",
                        styles["bullet"],
                    )
                ),
            ],
            bulletType="bullet",
            start="•",
        )
    )

    story.append(Spacer(1, 16))
    story.append(
        _callout(
            "<b>Final reminder:</b> This bot can lose money in Live mode. "
            "<b>Gains are not guaranteed. Invest at your own risk.</b> "
            "By using Live trading you accept responsibility for your keys, fees, and trade outcomes.",
            styles,
        )
    )
    story.append(
        Paragraph(
            f"Sol Dex Bot Trader v{_app_version()} — local use only · "
            f"Guide built {_guide_built_stamp()} · "
            "Regenerable via setup-bot-installer/generate_user_guide.py",
            styles["footer"],
        )
    )
    return story


def _add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(
        letter[0] / 2.0,
        0.5 * inch,
        f"Sol Dex Bot Trader User Guide v{_app_version()}  ·  page {doc.page}  ·  "
        "Gains not guaranteed — invest at your own risk",
    )
    canvas.restoreState()


def _write_pdf(out: Path, styles) -> None:
    """Write via temp file then replace — avoids WinError 22 / locks on direct overwrite."""
    import shutil
    import tempfile
    import time

    out.parent.mkdir(parents=True, exist_ok=True)
    story = build_story(styles)
    fd, tmp_name = tempfile.mkstemp(suffix=".pdf", prefix="soldex_guide_")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        doc = SimpleDocTemplate(
            str(tmp_path),
            pagesize=letter,
            leftMargin=0.75 * inch,
            rightMargin=0.75 * inch,
            topMargin=0.65 * inch,
            bottomMargin=0.75 * inch,
            title="Sol Dex Bot Trader User Guide",
            author="Sol Dex Bot Trader",
            creator=f"Sol Dex Bot Trader v{_app_version()}",
            subject=f"User guide built {_guide_built_stamp()}",
            # CreationDate / ModDate: reportlab stamps wall-clock now on build
        )
        doc.build(story, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
        last_err: OSError | None = None
        for attempt in range(8):
            try:
                if out.exists():
                    try:
                        out.unlink()
                    except OSError:
                        # Destination locked — overwrite in place via copy.
                        shutil.copy2(str(tmp_path), str(out))
                        break
                shutil.copy2(str(tmp_path), str(out))
                break
            except OSError as e:
                last_err = e
                time.sleep(0.35 * (attempt + 1))
        else:
            raise PermissionError(f"Could not write PDF to {out}: {last_err}") from last_err
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    OUT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    print(f"Guide version {_app_version()} — built {_guide_built_stamp()}")

    # Build story fresh per output — reportlab flowables are single-use.
    for out in (OUT_DOCS, OUT_INSTALLER, OUT_OUTPUT):
        _write_pdf(out, styles)


if __name__ == "__main__":
    main()
