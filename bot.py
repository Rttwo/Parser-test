import asyncio
import logging
import os
import json
import httpx
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from tokens import TOKENS as DEFAULT_TOKENS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ── Переменные окружения ──────────────────────────────────────
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]
CHECK_INTERVAL    = int(os.environ.get("CHECK_INTERVAL", "30"))
THRESHOLD_PERCENT = float(os.environ.get("THRESHOLD_PERCENT", "3.0"))
ALERT_COOLDOWN    = 300  # сек между повторными алертами по одному токену

# ── Хранилище токенов ─────────────────────────────────────────
TOKENS_FILE = "tokens_dynamic.json"
last_alert: dict[str, float] = {}


def load_tokens() -> list[dict]:
    """Загружаем токены из JSON, если нет — берём дефолтные"""
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    # Первый запуск — сохраняем дефолтные
    save_tokens(DEFAULT_TOKENS)
    return DEFAULT_TOKENS.copy()


def save_tokens(tokens: list[dict]):
    """Сохраняем токены в JSON"""
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


# Глобальный список токенов
TOKENS = load_tokens()


# ── DEX цены (GeckoTerminal) ─────────────────────────────────
# Все сети через GeckoTerminal (~5-10 сек задержка)
# Jupiter подключим позже как улучшение для Solana

# Маппинг наших названий сетей → GeckoTerminal network ID
GECKO_NETWORK = {
    "solana":   "solana",
    "ethereum": "eth",
    "bsc":      "bsc",
    "base":     "base",
    "ton":      "ton",
}

async def get_dex_prices(tokens: list[dict]) -> dict[str, float]:
    """Цены с DEX через GeckoTerminal.
    Endpoint: /api/v2/networks/{network}/tokens/multi/{addresses}
    Поддерживает до 30 адресов за раз.
    TON запрашивается по одному (спецсимволы в адресе).
    """
    prices = {}
    by_chain: dict[str, list[str]] = {}
    for t in tokens:
        by_chain.setdefault(t.get("chain", "solana"), []).append(t["mint"])

    headers = {"Accept": "application/json;version=20230302"}

    async with httpx.AsyncClient(timeout=15) as client:
        for chain, mints in by_chain.items():
            gecko_net = GECKO_NETWORK.get(chain, chain)
            for i in range(0, len(mints), 30):
                chunk = mints[i:i+30]

                # TON адреса содержат спецсимволы — запрашиваем по одному
                if chain == "ton":
                    for mint in chunk:
                        url = f"https://api.geckoterminal.com/api/v2/networks/{gecko_net}/tokens/{mint}"
                        try:
                            r = await client.get(url, headers=headers)
                            logger.info(f"TON запрос: status={r.status_code}")
                            r.raise_for_status()
                            resp_data = r.json()
                            attrs = resp_data.get("data", {}).get("attributes", {})
                            price_str = attrs.get("price_usd")
                            logger.info(f"TON {mint[:16]}: price_usd={price_str}, symbol={attrs.get('symbol')}")
                            if price_str and price_str != "null":
                                prices[mint] = float(price_str)
                                logger.info(f"TON цена: ${float(price_str):.8f}")
                            else:
                                logger.warning(f"TON {mint[:16]}: price_usd пустой или null")
                        except Exception as e:
                            logger.error(f"GeckoTerminal TON {mint[:16]}: {e}")
                    continue

                addrs = ",".join(chunk)
                url = f"https://api.geckoterminal.com/api/v2/networks/{gecko_net}/tokens/multi/{addrs}"
                try:
                    r = await client.get(url, headers=headers)
                    r.raise_for_status()
                    items = r.json().get("data", [])
                except Exception as e:
                    logger.error(f"GeckoTerminal {chain}: {e}")
                    continue

                for item in items:
                    attrs = item.get("attributes", {})
                    addr = attrs.get("address", "")
                    price_str = attrs.get("price_usd")
                    if not addr or not price_str:
                        continue
                    if chain not in ("solana", "ton"):
                        addr = addr.lower()
                    try:
                        prices[addr] = float(price_str)
                    except (ValueError, TypeError):
                        pass
    return prices


async def get_dex_info(mint: str, chain: str) -> dict | None:
    """Получаем инфу по токену с GeckoTerminal для проверки при /add"""
    gecko_net = GECKO_NETWORK.get(chain, chain)
    headers = {"Accept": "application/json;version=20230302"}
    async with httpx.AsyncClient(timeout=15) as client:
        url = f"https://api.geckoterminal.com/api/v2/networks/{gecko_net}/tokens/{mint}"
        try:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            attrs = r.json().get("data", {}).get("attributes", {})
        except Exception:
            return None
    if not attrs or not attrs.get("price_usd"):
        return None
    try:
        vol = float(attrs.get("volume_usd", {}).get("h24") or 0)
        return {
            "symbol": attrs.get("symbol", ""),
            "name":   attrs.get("name", ""),
            "price":  float(attrs.get("price_usd")),
            "volume": vol,
            "liq":    float(attrs.get("total_reserve_in_usd") or 0),
            "dex":    "geckoterminal",
        }
    except (ValueError, TypeError):
        return None


# ── MEXC фьючерсы ─────────────────────────────────────────────
async def get_mexc_prices(symbols: list[str]) -> dict[str, float]:
    """Цены с MEXC Futures.
    На MEXC многие мемкоины торгуются с префиксом 1000 (1000BONK, 1000SHIB и тд).
    Логика: сначала ищем точное совпадение, затем 1000SYMBOL автоматически.
    """
    prices = {}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get("https://contract.mexc.com/api/v1/contract/ticker")
            r.raise_for_status()
            data = r.json().get("data", [])
            all_prices = {}
            for item in data:
                sym = item.get("symbol", "")
                price = item.get("lastPrice")
                if not price:
                    continue
                try:
                    price_f = float(price)
                except (ValueError, TypeError):
                    continue
                # Стандартный формат: BONK_USDT → BONK
                if sym.endswith("_USDT"):
                    all_prices[sym.replace("_USDT", "")] = price_f
                # Нестандартный формат без подчёркивания: 熊猫头USDT → 熊猫头
                elif sym.endswith("USDT") and not sym.endswith("_USDT"):
                    all_prices[sym[:-4]] = price_f

            for symbol in symbols:
                # 1. Прямое совпадение
                if symbol in all_prices:
                    prices[symbol] = all_prices[symbol]
                # 2. Автоматически пробуем 1000SYMBOL
                elif f"1000{symbol}" in all_prices:
                    prices[symbol] = all_prices[f"1000{symbol}"]
                    logger.info(f"MEXC: {symbol} найден как 1000{symbol}")
                else:
                    logger.warning(f"MEXC Futures: {symbol} не найден")
        except Exception as e:
            logger.error(f"MEXC Futures: {e}")
    return prices


def calc_diff(dex: float, cex: float) -> float:
    return (dex - cex) / cex * 100


# ── Telegram алерт ────────────────────────────────────────────
async def send_alert(bot: Bot, name: str, symbol: str,
                     dex_price: float, cex_price: float, diff: float,
                     mint: str, chain: str):
    if diff > 0:
        arrow, position, reason = "🟢", "🟩 ЛОНГ на MEXC", "DEX вырос — MEXC догонит вверх"
    else:
        arrow, position, reason = "🔴", "🟥 ШОРТ на MEXC", "DEX упал — MEXC догонит вниз"

    dex_url  = f"https://dexscreener.com/{chain}/{mint}"
    mexc_url = f"https://www.mexc.com/futures/{symbol}_USDT"

    msg = (
        f"{arrow} *Арбитраж! {position}*\n\n"
        f"🪙 {name} ({symbol})\n"
        f"📊 Разница: *{diff:+.2f}%*\n"
        f"─────────────────\n"
        f"🌊 DEX: `${dex_price:.8f}`\n"
        f"🏦 MEXC: `${cex_price:.8f}`\n"
        f"_{reason}_\n"
        f"─────────────────\n"
        f"📈 [График DEX]({dex_url})\n"
        f"📊 [Фьючерс MEXC]({mexc_url})"
    )
    await bot.send_message(
        chat_id=CHAT_ID, text=msg,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )
    logger.info(f"Алерт: {name} diff={diff:+.2f}%")


# ── Основной цикл проверки ────────────────────────────────────
async def check_prices(bot: Bot):
    import time
    logger.info(f"Проверяем {len(TOKENS)} токенов...")

    dex_prices  = await get_dex_prices(TOKENS)
    mexc_prices = await get_mexc_prices([t["symbol"] for t in TOKENS])

    now = time.time()
    for token in TOKENS:
        mint   = token["mint"]
        symbol = token["symbol"]
        name   = token.get("name", symbol)
        chain  = token.get("chain", "solana")
        lookup = mint.lower() if chain != "solana" else mint

        dex_price  = dex_prices.get(lookup)
        cex_price  = mexc_prices.get(symbol)

        if dex_price is None:
            logger.warning(f"{name}: нет цены на DEX")
            continue
        if cex_price is None:
            logger.warning(f"{name}: нет цены на MEXC")
            continue

        diff = calc_diff(dex_price, cex_price)
        logger.info(f"{symbol}: DEX=${dex_price:.8f} MEXC=${cex_price:.8f} diff={diff:+.2f}%")

        if abs(diff) > 50:
            logger.warning(f"{symbol}: спред {diff:+.2f}% слишком большой, пропускаем")
            continue

        if abs(diff) >= THRESHOLD_PERCENT:
            if (now - last_alert.get(symbol, 0)) >= ALERT_COOLDOWN:
                await send_alert(bot, name, symbol, dex_price, cex_price, diff, mint, chain)
                last_alert[symbol] = now
            else:
                logger.info(f"{symbol}: cooldown активен")


# ══════════════════════════════════════════════════════════════
#  КОМАНДЫ TELEGRAM
# ══════════════════════════════════════════════════════════════

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add <контракт> <символ> <сеть>
    Пример: /add 0x6982508145454Ce325dDbE47a25d4ec3d2311933 PEPE ethereum
    Сети: solana | ethereum | bsc | base
    """
    args = context.args
    if len(args) != 3:
        await update.message.reply_text(
            "❌ Формат: `/add <контракт> <символ> <сеть>`\n"
            "Пример: `/add 0x6982...933 PEPE ethereum`\n"
            "Сети: `solana` `ethereum` `bsc` `base`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    mint, symbol, chain = args[0], args[1].upper(), args[2].lower()
    symbol_orig = args[1]  # сохраняем оригинальный символ (для иероглифов)

    if chain not in ("solana", "ethereum", "bsc", "base"):
        await update.message.reply_text("❌ Сеть должна быть: `solana` `ethereum` `bsc` `base`",
                                        parse_mode=ParseMode.MARKDOWN)
        return

    # Проверяем что токен не добавлен уже
    if any(t["mint"].lower() == mint.lower() for t in TOKENS):
        await update.message.reply_text(f"⚠️ Токен с адресом `{mint[:10]}...` уже в списке",
                                        parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text(f"🔍 Проверяю `{symbol_orig}` на DexScreener и MEXC...",
                                    parse_mode=ParseMode.MARKDOWN)

    # ── Проверяем DEX ─────────────────────────────────────────
    dex_info = await get_dex_info(mint, chain)
    if not dex_info:
        await update.message.reply_text(
            f"❌ `{mint[:10]}...` не найден на DexScreener в сети `{chain}`\n"
            f"Проверь адрес и сеть",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if dex_info["volume"] < 50_000:
        await update.message.reply_text(
            f"❌ Объём торгов за 24ч слишком маленький: `${dex_info['volume']:,.0f}`\n"
            f"Нужно минимум `$50,000`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Проверяем MEXC ────────────────────────────────────────
    mexc_prices = await get_mexc_prices([symbol_orig])
    mexc_price  = mexc_prices.get(symbol_orig)

    if not mexc_price:
        await update.message.reply_text(
            f"❌ `{symbol_orig}_USDT` не найден на MEXC фьючерсах\n"
            f"Проверь тикер на MEXC",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Проверяем что цена совпадает (тот же токен) ───────────
    diff_pct = abs(dex_info["price"] - mexc_price) / mexc_price * 100
    if diff_pct > 10:
        await update.message.reply_text(
            f"⚠️ Цены сильно расходятся — возможно не тот токен!\n"
            f"DEX: `${dex_info['price']:.8f}`\n"
            f"MEXC: `${mexc_price:.8f}`\n"
            f"Разница: `{diff_pct:.1f}%` (норма < 10%)\n\n"
            f"Всё равно добавить? Напиши `/forceadd {mint} {symbol_orig} {chain}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Всё ок — добавляем ───────────────────────────────────
    new_token = {
        "mint":   mint,
        "symbol": symbol_orig,
        "name":   dex_info.get("name") or symbol_orig,
        "chain":  chain,
    }
    TOKENS.append(new_token)
    save_tokens(TOKENS)

    await update.message.reply_text(
        f"✅ *{dex_info['name']} ({symbol_orig})* добавлен!\n\n"
        f"🌊 DEX цена: `${dex_info['price']:.8f}`\n"
        f"🏦 MEXC цена: `${mexc_price:.8f}`\n"
        f"📊 Объём 24h: `${dex_info['volume']:,.0f}`\n"
        f"💧 Ликвидность: `${dex_info['liq']:,.0f}`\n"
        f"🔀 DEX: `{dex_info['dex']}`\n"
        f"⛓ Сеть: `{chain}`\n"
        f"📋 Всего токенов: `{len(TOKENS)}`",
        parse_mode=ParseMode.MARKDOWN
    )
    logger.info(f"Добавлен токен: {symbol_orig} {mint} {chain}")


async def cmd_forceadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительное добавление без проверки цены"""
    args = context.args
    if len(args) != 3:
        await update.message.reply_text("❌ Формат: `/forceadd <контракт> <символ> <сеть>`",
                                        parse_mode=ParseMode.MARKDOWN)
        return

    mint, symbol, chain = args[0], args[1], args[2].lower()
    new_token = {"mint": mint, "symbol": symbol, "name": symbol, "chain": chain}
    TOKENS.append(new_token)
    save_tokens(TOKENS)
    await update.message.reply_text(
        f"✅ `{symbol}` принудительно добавлен (без проверки цены)\nВсего: `{len(TOKENS)}`",
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/remove <символ> — удалить токен"""
    if not context.args:
        await update.message.reply_text("❌ Формат: `/remove <символ>`\nПример: `/remove PEPE`",
                                        parse_mode=ParseMode.MARKDOWN)
        return

    symbol = context.args[0]
    before = len(TOKENS)
    TOKENS[:] = [t for t in TOKENS if t["symbol"] != symbol]

    if len(TOKENS) < before:
        save_tokens(TOKENS)
        await update.message.reply_text(f"✅ `{symbol}` удалён. Осталось токенов: `{len(TOKENS)}`",
                                        parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Удалён токен: {symbol}")
    else:
        await update.message.reply_text(f"❌ Токен `{symbol}` не найден в списке",
                                        parse_mode=ParseMode.MARKDOWN)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/list — показать все токены"""
    if not TOKENS:
        await update.message.reply_text("📋 Список токенов пуст")
        return

    lines = [f"📋 *Токены в мониторинге* ({len(TOKENS)}):\n"]
    for i, t in enumerate(TOKENS, 1):
        chain_emoji = {"solana": "◎", "ethereum": "Ξ", "bsc": "🟡", "base": "🔵"}.get(t["chain"], "⛓")
        lines.append(f"{i}. {chain_emoji} `{t['symbol']}` — {t['chain']}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — проверить все токены прямо сейчас"""
    await update.message.reply_text("🔍 Проверяю все токены...", parse_mode=ParseMode.MARKDOWN)

    dex_prices  = await get_dex_prices(TOKENS)
    mexc_prices = await get_mexc_prices([t["symbol"] for t in TOKENS])

    ok, no_dex, no_mexc = [], [], []
    for t in TOKENS:
        symbol = t["symbol"]
        chain  = t.get("chain", "solana")
        lookup = t["mint"].lower() if chain != "solana" else t["mint"]
        has_dex  = lookup in dex_prices
        has_mexc = symbol in mexc_prices
        if has_dex and has_mexc:
            dex = dex_prices[lookup]
            cex = mexc_prices[symbol]
            diff = calc_diff(dex, cex)
            ok.append(f"✅ `{symbol}` diff.{diff:+.2f}%")
        elif not has_dex:
            no_dex.append(f"❌ `{symbol}` нет DEX")
        else:
            no_mexc.append(f"⚠️ `{symbol}` нет MEXC")

    lines = [f"📊 *Статус* — {len(ok)}/{len(TOKENS)} активных\n"]
    lines += ok
    if no_dex:
        lines.append("\n*Проблемы:*")
        lines += no_dex + no_mexc

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — список команд"""
    text = (
        "🤖 *Команды бота:*\n\n"
        "`/add <контракт> <символ> <сеть>` — добавить токен\n"
        "_Пример: /add 0x6982...933 PEPE ethereum_\n\n"
        "`/remove <символ>` — удалить токен\n"
        "_Пример: /remove PEPE_\n\n"
        "`/list` — список всех токенов\n\n"
        "`/status` — проверить цены прямо сейчас\n\n"
        "`/forceadd <контракт> <символ> <сеть>` — добавить без проверки цены\n\n"
        "*Сети:* `solana` `ethereum` `bsc` `base`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── Запуск ────────────────────────────────────────────────────
async def verify_tokens_on_start(bot: Bot):
    dex_prices  = await get_dex_prices(TOKENS)
    mexc_prices = await get_mexc_prices([t["symbol"] for t in TOKENS])

    ok, no_dex, no_mexc = [], [], []
    for t in TOKENS:
        symbol = t["symbol"]
        chain  = t.get("chain", "solana")
        lookup = t["mint"].lower() if chain != "solana" else t["mint"]
        if lookup in dex_prices and symbol in mexc_prices:
            ok.append(symbol)
        elif lookup not in dex_prices:
            no_dex.append(f"❌ {symbol}")
        else:
            no_mexc.append(f"⚠️ {symbol}")

    lines = [f"🤖 *Бот запущен — {len(ok)}/{len(TOKENS)} активных*",
             f"⏱ {CHECK_INTERVAL}s . 🎯 {THRESHOLD_PERCENT}%\n",
             f"*Мониторю:* {', '.join(ok) if ok else 'нет'}"]
    if no_dex or no_mexc:
        lines.append("\n*Проблемы:*")
        lines += no_dex + no_mexc
    lines.append("\n💡 /help — список команд")

    await bot.send_message(chat_id=CHAT_ID, text="\n".join(lines),
                           parse_mode=ParseMode.MARKDOWN)


async def price_loop(bot: Bot):
    """Бесконечный цикл проверки цен"""
    while True:
        try:
            await check_prices(bot)
        except Exception as e:
            logger.error(f"Ошибка в цикле: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(app: Application):
    """Вызывается после инициализации — отправляем стартовое сообщение"""
    await verify_tokens_on_start(app.bot)
    # Запускаем цикл проверки цен как фоновую задачу
    asyncio.create_task(price_loop(app.bot))


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("add",      cmd_add))
    app.add_handler(CommandHandler("forceadd", cmd_forceadd))
    app.add_handler(CommandHandler("remove",   cmd_remove))
    app.add_handler(CommandHandler("list",     cmd_list))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("help",     cmd_help))

    logger.info("Запуск бота...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
