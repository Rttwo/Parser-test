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


async def get_jupiter_prices(mints: list[str]) -> dict[str, float]:
    """Цены с Jupiter Price API (DEX-агрегатор Solana)"""
    ids = ",".join(mints)
    url = f"https://lite.jupiter.aggregator.com/price/v2?ids={ids}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json().get("data", {})
            return {
                mint: float(info["price"])
                for mint, info in data.items()
                if info and info.get("price")
            }
    except Exception as e:
        logger.error(f"Jupiter API error: {e}")
        return {}


async def get_mexc_prices(symbols: list[str]) -> dict[str, float]:
    """Цены с MEXC публичного REST API"""
    prices = {}
    url = "https://api.mexc.com/api/v3/ticker/price"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
            all_prices = {item["symbol"]: float(item["price"]) for item in r.json()}
            for symbol in symbols:
                key = f"{symbol}USDT"
                if key in all_prices:
                    prices[symbol] = all_prices[key]
                else:
                    logger.warning(f"MEXC: {key} не найден")
    except Exception as e:
        logger.error(f"MEXC API error: {e}")
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
        f"🌊 Jupiter (DEX): `${dex_price:.8f}`\n"
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

    mints   = [t["mint"]   for t in TOKENS]
    symbols = [t["symbol"] for t in TOKENS]

    logger.info(f"Проверяем {len(TOKENS)} токенов...")

    jup_prices, mexc_prices = await asyncio.gather(
        get_jupiter_prices(mints),
        get_mexc_prices(symbols)
    )

    now = time.time()
    for token in TOKENS:
        mint   = token["mint"]
        symbol = token["symbol"]
        name   = token["name"]

        dex_price = jup_prices.get(mint)
        cex_price = mexc_prices.get(symbol)

        if dex_price is None:
            logger.warning(f"{name}: нет цены с Jupiter")
            continue
        if cex_price is None:
            logger.warning(f"{name}: нет цены с MEXC")
            continue

        diff = calc_diff(dex_price, cex_price)
        logger.info(f"{name}: DEX=${dex_price:.8f} | CEX=${cex_price:.8f} | diff={diff:+.2f}%")

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
            f"Слежу за Jupiter vs MEXC..."
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
