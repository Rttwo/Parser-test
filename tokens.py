# ============================================================
#  МЕМКОИНЫ ДЛЯ МОНИТОРИНГА
#  Критерии: есть фьючерс на MEXC + объём DEX > $50k/сутки
#  Адреса верифицированы через Etherscan / BscScan / BaseScan / Solscan
# ============================================================

TOKENS = [

    # ══════════════════════════════════════════════════════
    #  SOLANA
    # ══════════════════════════════════════════════════════

    # ✅ Тикер MEXC: BONKUSDT | Solscan верифицирован
    {"mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
     "symbol": "BONK", "name": "Bonk", "chain": "solana"},

    # ✅ Тикер MEXC: WIFUSDT | Solscan верифицирован
    {"mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
     "symbol": "WIF", "name": "dogwifhat", "chain": "solana"},

    # ✅ Тикер MEXC: POPCATUSDT | Solscan верифицирован
    {"mint": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
     "symbol": "POPCAT", "name": "Popcat", "chain": "solana"},

    # ✅ Тикер MEXC: MEWUSDT | Solscan верифицирован
    {"mint": "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",
     "symbol": "MEW", "name": "cat in a dogs world", "chain": "solana"},

    # ✅ Тикер MEXC: TRUMPUSDT | Solscan верифицирован
    {"mint": "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",
     "symbol": "TRUMP", "name": "OFFICIAL TRUMP", "chain": "solana"},

    # ✅ Тикер MEXC: FARTCOINUSDT | Solscan верифицирован
    {"mint": "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump",
     "symbol": "FARTCOIN", "name": "Fartcoin", "chain": "solana"},

    # ✅ Тикер MEXC: PENGUUSDT | Solscan верифицирован
    {"mint": "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv",
     "symbol": "PENGU", "name": "Pudgy Penguins", "chain": "solana"},

    # ✅ Тикер MEXC: PNUTUSDT | Solscan верифицирован
    {"mint": "HLptm5e6rTgh4EKgDpYFrnRHbjpkMyVdEeREEa2G7rf9",
     "symbol": "PNUT", "name": "Peanut the Squirrel", "chain": "solana"},

    # ══════════════════════════════════════════════════════
    #  ETHEREUM
    # ══════════════════════════════════════════════════════

    # ✅ Тикер MEXC: PEPEUSDT | Etherscan verified + Coinbase official
    {"mint": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
     "symbol": "PEPE", "name": "Pepe", "chain": "ethereum"},

    # ✅ Тикер MEXC: SHIBUSDT | Etherscan verified
    {"mint": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
     "symbol": "SHIB", "name": "Shiba Inu", "chain": "ethereum"},

    # ✅ Тикер MEXC: FLOKIUSDT | CoinGecko official ETH адрес
    {"mint": "0xcf0C122c6b73FF809C693DB761e7BaeBe62b6a2E",
     "symbol": "FLOKI", "name": "Floki", "chain": "ethereum"},

    # ✅ Тикер MEXC: TURBOUSDT | Etherscan verified
    {"mint": "0xAAAe846046481E13b27E4fE89E5f67d8d43BD9C1",
     "symbol": "TURBO", "name": "Turbo", "chain": "ethereum"},

    # ✅ Тикер MEXC: SPXUSDT | Etherscan verified — SPX6900
    {"mint": "0xE0f63A424a4439cBE457D80E4f4b51aD25b2c56F",
     "symbol": "SPX", "name": "SPX6900", "chain": "ethereum"},

    # ✅ Тикер MEXC: ASTEROIDUSDT (верхний, объём $3.5M) | Etherscan verified
    {"mint": "0xf280B16EF293D8e534e370794ef26bF312694126",
     "symbol": "ASTEROID", "name": "Asteroid", "chain": "ethereum"},

    # ══════════════════════════════════════════════════════
    #  BASE
    # ══════════════════════════════════════════════════════

    # ✅ Тикер MEXC: BRETTUSDT | BaseScan verified + CoinGecko
    {"mint": "0x532f27101965dd16442E59d40670FaF5eBB142E4",
     "symbol": "BRETT", "name": "Brett", "chain": "base"},

    # ══════════════════════════════════════════════════════
    #  BNB CHAIN
    # ══════════════════════════════════════════════════════

    # ✅ Тикер MEXC: DOGEUSDT | BscScan verified — Binance-Peg DOGE
    {"mint": "0xbA2aE424d960c26247Dd6c32edC70B295c744C43",
     "symbol": "DOGE", "name": "Dogecoin (BNB)", "chain": "bsc"},

    # ✅ Тикер MEXC: 熊猫头USDT | BscScan verified — MEXC Meme+ листинг
    {"mint": "0xf3525965a4aD3ca0AC13f4D2F237113691194444",
     "symbol": "熊猫头", "name": "熊猫头 (Panda Head)", "chain": "bsc"},

]
