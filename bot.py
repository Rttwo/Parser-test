import asyncio
import logging
import os
import httpx
from telegram import Bot
from telegram.constants import ParseMode
from tokens import TOKENS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Секреты — только из переменных окружения (Railway)
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]
CHECK_INTERVAL    = int(os.environ.get("CHECK_INTERVAL", "30"))
THRESHOLD_PERCENT = float(os.environ.get("THRESHOLD_PERCENT", "3.0"))

# Кэш: symbol -> timestamp последнего алерта
last_alert: dict[str, float] = {}
ALERT_COOLDOWN = 300  # 5 минут между повторными алертами по одному токену


async def get_dex_prices(tokens: list[dict]) -> dict[str, float]:
    """Цены с DexScreener — поддержка Solana, Ethereum, BNB, Base"""
    prices = {}
    # Группируем токены по сети
    by_chain: dict[str, list[str]] = {}
    for t in tokens:
        chain = t.get("chain", "solana")
        # DexScreener использует разные названия сетей
        chain_map = {"bsc": "bsc", "ethereum": "ethereum", "base": "base", "solana": "solana"}
        dex_chain = chain_map.get(chain, "solana")
        by_chain.setdefault(dex_chain, []).append(t["mint"])

    chunk_size = 30
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for chain, mints in by_chain.items():
                for i in range(0, len(mints), chunk_size):
                    chunk = mints[i:i + chunk_size]
                    url = f"https://api.dexscreener.com/tokens/v1/{chain}/{','.join(chunk)}"
                    try:
                        r = await client.get(url)
                        r.raise_for_status()
                        pairs = r.json()
                    except Exception as e:
                        logger.error(f"DexScreener {chain} error: {e}")
                        continue
                    best: dict[str, tuple[float, float]] = {}
                    for pair in pairs:
                        quote_sym = pair.get("quoteToken", {}).get("symbol", "")
                        if quote_sym not in ("USDC", "USDT", "WETH", "WBNB"):
                            continue
                        base_addr = pair.get("baseToken", {}).get("address", "")
                        # Нормализуем адрес (ETH/BNB/Base — lowercase)
                        if chain != "solana":
                            base_addr = base_addr.lower()
                        price_str = pair.get("priceUsd")
                        liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
                        if liquidity < 50_000:
                            continue
                        if base_addr and price_str:
                            try:
                                price = float(price_str)
                                if base_addr not in best or liquidity > best[base_addr][1]:
                                    best[base_addr] = (price, liquidity)
                            except (ValueError, TypeError):
                                pass
                    for addr, (price, _) in best.items():
                        prices[addr] = price
    except Exception as e:
        logger.error(f"DexScreener error: {e}")
    return prices


async def get_mexc_prices(symbols: list[str]) -> dict[str, float]:
    """Цены с MEXC Futures (перпетуальные контракты)"""
    prices = {}
    url = "https://contract.mexc.com/api/v1/contract/ticker"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json().get("data", [])
            # Строим словарь: "BTC_USDT" -> lastPrice
            all_prices = {}
            for item in data:
                symbol = item.get("symbol", "")
                price = item.get("lastPrice")
                if symbol and price:
                    try:
                        all_prices[symbol] = float(price)
                    except (ValueError, TypeError):
                        pass
            for symbol in symbols:
                key = f"{symbol}_USDT"
                if key in all_prices:
                    prices[symbol] = all_prices[key]
                else:
                    logger.warning(f"MEXC Futures: {key} не найден (нет фьючерса)")
    except Exception as e:
        logger.error(f"MEXC Futures API error: {e}")
    return prices


def calc_diff(price_dex: float, price_cex: float) -> float:
    return ((price_dex - price_cex) / price_cex) * 100


async def send_alert(bot: Bot, token_name: str, symbol: str,
                     dex_price: float, cex_price: float, diff: float):
    # DEX вырос → MEXC догонит вверх → ЛОНГ
    # DEX упал  → MEXC догонит вниз  → ШОРТ
    if diff > 0:
        arrow = "🟢"
        direction = "📈 DEX > CEX"
        position = "🟩 ЛОНГ на MEXC"
        reason = "DEX вырос — MEXC будет догонять вверх"
    else:
        arrow = "🔴"
        direction = "📉 DEX < CEX"
        position = "🟥 ШОРТ на MEXC"
        reason = "DEX упал — MEXC будет догонять вниз"

    msg = (
        f"{arrow} *Арбитраж обнаружен!*\n\n"
        f"🪙 Токен: `{token_name}` ({symbol})\n"
        f"📊 Разница: *{diff:+.2f}%*\n"
        f"─────────────────\n"
        f"🌊 DexScreener (DEX): `${dex_price:.8f}`\n"
        f"🏦 MEXC (CEX):    `${cex_price:.8f}`\n"
        f"─────────────────\n"
        f"{direction}\n"
        f"─────────────────\n"
        f"*{position}*\n"
        f"_{reason}_\n"
        f"⚡️ Порог: ≥ {THRESHOLD_PERCENT}%"
    )
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
    logger.info(f"Алерт отправлен: {token_name} diff={diff:+.2f}%")


async def check_prices(bot: Bot):
    import time

    logger.info(f"Проверяем {len(TOKENS)} токенов...")

    dex_prices, mexc_prices = await asyncio.gather(
        get_dex_prices(TOKENS),
        get_mexc_prices([t["symbol"] for t in TOKENS])
    )

    now = time.time()
    for token in TOKENS:
        mint   = token["mint"]
        symbol = token["symbol"]
        name   = token["name"]
        chain  = token.get("chain", "solana")

        # ETH/BNB/Base адреса — lowercase для сравнения
        lookup_key = mint.lower() if chain != "solana" else mint

        dex_price = dex_prices.get(lookup_key)
        cex_price = mexc_prices.get(symbol)

        if dex_price is None:
            logger.warning(f"{name}: нет цены с DexScreener")
            continue
        if cex_price is None:
            logger.warning(f"{name}: нет цены с MEXC")
            continue

        diff = calc_diff(dex_price, cex_price)
        logger.info(f"{name}: DEX=${dex_price:.8f} | CEX=${cex_price:.8f} | diff={diff:+.2f}%")

        # Защита от мусорных данных — спред >50% это скорее всего баг
        if abs(diff) > 50:
            logger.warning(f"{name}: спред {diff:+.2f}% слишком большой, пропускаем (вероятно баг данных)")
            continue

        if abs(diff) >= THRESHOLD_PERCENT:
            if (now - last_alert.get(symbol, 0)) >= ALERT_COOLDOWN:
                await send_alert(bot, name, symbol, dex_price, cex_price, diff)
                last_alert[symbol] = now
            else:
                logger.info(f"{name}: cooldown активен, пропускаем")


async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    me = await bot.get_me()
    logger.info(f"Бот запущен: @{me.username}")

    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"🤖 *Бот арбитража запущен*\n\n"
            f"📋 Токенов: {len(TOKENS)}\n"
            f"⏱ Интервал: каждые {CHECK_INTERVAL} сек\n"
            f"🎯 Порог: {THRESHOLD_PERCENT}%\n\n"
            f"Слежу за DexScreener vs MEXC..."
        ),
        parse_mode=ParseMode.MARKDOWN
    )

    while True:
        try:
            await check_prices(bot)
        except Exception as e:
            logger.error(f"Ошибка в цикле: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
