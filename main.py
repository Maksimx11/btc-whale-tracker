import json
import time
import requests
import os
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# === ХАРДКОД ДЛЯ ТЕСТА (НЕ ИСПОЛЬЗОВАТЬ В ПРОДЕ!) ===
SUPABASE_URL = "https://bbzynftosxppxxsnlozo.supabase.co"
SUPABASE_KEY = "sb_publishable_4dpzEKOxERFrNDU-hgqeiQ_GvAKtTcd"

MIN_USD_AMOUNT = 50_000_000  # $50 млн
MIN_BTC_AMOUNT = 500  # 500 BTC
STATE_FILE = Path("tracker_state.json")
SCAN_INTERVAL = 30  # Секунды между циклами
REQUEST_TIMEOUT = 10

# Указываем Render использовать порт 8080
os.environ["PORT"] = "8080"


# === HEALTH CHECK СЕРВЕР (Чтобы Render не убивал процесс) ===
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Whale Tracker is running!")


def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"🌐 Health check server started on port {port}")
    server.serve_forever()


# === СОСТОЯНИЕ ===
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(STATE_FILE)


# === API ===
def get_btc_price() -> float:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    try:
        res = requests.get(url, timeout=REQUEST_TIMEOUT).json()
        price = float(res["bitcoin"]["usd"])
        print(f"💵 Курс BTC: ${price:,.2f}")
        return price
    except Exception as e:
        print(f"⚠️ Ошибка курса: {e}. Дефолт $65,000")
        return 65000.0


def get_monitored_wallets() -> list[dict]:
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    url = f"{SUPABASE_URL}/rest/v1/exchange_wallets?blockchain=eq.BTC&select=address,label"
    try:
        res = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        wallets = res.json()
        print(f"📂 Кошельков в базе: {len(wallets)}")
        return wallets
    except Exception as e:
        print(f"❌ Ошибка загрузки кошельков: {e}")
        return []


def save_whale_transaction(tx_data: dict) -> bool:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=ignore-duplicates"
    }
    payload = {
        "tx_hash": tx_data["tx_hash"],
        "wallet_address": tx_data["wallet_address"],
        "direction": tx_data["direction"],
        "amount_crypto": tx_data["amount_btc"],
        "amount_usd": tx_data["amount_usd"]
    }
    url = f"{SUPABASE_URL}/rest/v1/whale_transactions"
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if res.status_code == 201:
            print(
                f"💾 Кит сохранен: {payload['direction']} {payload['amount_crypto']:.4f} BTC (${payload['amount_usd']:,.0f})")
            return True
        elif res.status_code == 409:
            print(f"ℹ️ Дубликат пропущен: {payload['tx_hash'][:16]}...")
            return False
        else:
            print(f"⚠️ Ошибка ({res.status_code}): {res.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ Сетевая ошибка сохранения: {e}")
        return False


# === ЯДРО ===
def analyze_transaction(tx: dict, wallet_address: str) -> dict | None:
    tx_hash = tx.get("txid")
    vouts = tx.get("vout", [])
    vins = tx.get("vin", [])

    is_sender = any(
        vin.get("prevout", {}).get("scriptpubkey_address") == wallet_address
        for vin in vins if vin.get("prevout")
    )
    received_satoshis = sum(
        vout["value"] for vout in vouts
        if vout.get("scriptpubkey_address") == wallet_address
    )

    if is_sender:
        sent_satoshis = sum(
            vout["value"] for vout in vouts
            if vout.get("scriptpubkey_address") != wallet_address
        )
        if sent_satoshis > 0:
            return {"tx_hash": tx_hash, "direction": "OUT", "satoshis": sent_satoshis}

    if received_satoshis > 0 and not is_sender:
        return {"tx_hash": tx_hash, "direction": "IN", "satoshis": received_satoshis}

    return None


def scan_wallet(address: str, label: str, btc_price: float, state: dict) -> int:
    url = f"https://mempool.space/api/address/{address}/txs"
    try:
        res = requests.get(url, timeout=REQUEST_TIMEOUT)
        if res.status_code == 429:
            print(f"⏳ Rate limit mempool.space. Пропуск {label}")
            return 0
        res.raise_for_status()
        txs = res.json()
    except Exception as e:
        print(f"❌ Ошибка сканирования {label}: {e}")
        return 0

    if not isinstance(txs, list):
        return 0

    last_known_hash = state.get(address)
    new_txs_found = 0

    for tx in txs:
        tx_hash = tx["txid"]
        if tx_hash == last_known_hash:
            break

        analysis = analyze_transaction(tx, address)
        if analysis is None:
            continue

        amount_btc = analysis["satoshis"] / 1e8
        amount_usd = amount_btc * btc_price

        if amount_usd >= MIN_USD_AMOUNT or amount_btc >= MIN_BTC_AMOUNT:
            saved = save_whale_transaction({
                "tx_hash": tx_hash,
                "wallet_address": address,
                "direction": analysis["direction"],
                "amount_btc": round(amount_btc, 8),
                "amount_usd": round(amount_usd, 2)
            })
            if saved:
                new_txs_found += 1

    if txs:
        state[address] = txs[0]["txid"]

    return new_txs_found


# === ЗАПУСК ===
def main():
    # Запускаем веб-сервер в отдельном потоке
    threading.Thread(target=start_health_server, daemon=True).start()

    print("🐋 Whale Tracker v2.0 (Render Mode)")
    print(f"🎯 Порог: ${MIN_USD_AMOUNT:,} или {MIN_BTC_AMOUNT} BTC")

    state = load_state()

    while True:
        cycle_start = time.time()
        btc_price = get_btc_price()
        wallets = get_monitored_wallets()

        total_new = 0
        for wallet in wallets:
            count = scan_wallet(wallet["address"], wallet["label"], btc_price, state)
            total_new += count
            time.sleep(1)

        save_state(state)

        elapsed = time.time() - cycle_start
        print(f"✅ Цикл за {elapsed:.1f}с | Новых китов: {total_new}")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()