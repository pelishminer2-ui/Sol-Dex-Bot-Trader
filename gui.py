"""Browser GUI entry point (alias for app.py)."""



from app import HOST, PORT, app, get_firewall_stats
from config import PROJECT_ROOT
from tx_authorizer import get_transfer_guard_stats



if __name__ == "__main__":

    fw = get_firewall_stats()
    tg = get_transfer_guard_stats()
    print(
        f"\n{'=' * 60}\n"
        f"  Solana Mover Trading Bot — Web GUI\n"
        f"  Project: {PROJECT_ROOT}\n"
        f"  Open: http://127.0.0.1:{PORT}\n"
        f"\n"
        f"  SECURITY: Firewall active — localhost only ({HOST}).\n"
        f"  Transfer guard: {'active' if tg.get('active') else 'DISABLED'} — Jupiter swaps only.\n"
        f"  Do NOT expose port {PORT} publicly.\n"
        f"  Rate limit: {fw['rate_limit_per_min']} req/min per IP.\n"
        f"  Your private key stays in server memory only.\n"
        f"{'=' * 60}\n"
    )

    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)

