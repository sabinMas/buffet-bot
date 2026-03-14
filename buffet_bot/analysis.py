"""LLM orchestration — query models, run analysis, place orders."""
import json
from concurrent.futures import ThreadPoolExecutor

import ollama
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from buffet_bot.globals import (
    console, MODELS, MODEL_COLORS, STRATEGY_PROMPTS, trading_client,
)
from buffet_bot.db import log_recommendation
from buffet_bot.data import (
    get_buffett_metrics, get_analyst_consensus, get_tech_indicators,
    get_realtime_data, _fetch_fred_data, get_recent_news, analyze_news_sentiment,
    _yf_semaphore,
)
from buffet_bot.backtest import get_multiframe_signals
from buffet_bot.insiders import fetch_insider_transactions, insider_prompt_block


def _query_llms_freeform(prompt_text, primary_model):
    """Query both LLMs with a plain-text prompt, returning raw text responses."""
    models_to_query = [primary_model]
    if primary_model != MODELS[1]:
        models_to_query.append(MODELS[1])

    responses = {}
    for model in models_to_query:
        color = MODEL_COLORS.get(model, 'white')
        with console.status(f"[{color}]Querying {model}...[/{color}]"):
            try:
                resp = ollama.chat(
                    model=model,
                    messages=[{'role': 'user', 'content': prompt_text}],
                    options={'temperature': 0.5},
                )
                responses[model] = resp['message']['content'].strip()
            except Exception as e:
                responses[model] = f"Error: {e}"
    return responses


def _run_analysis(ticker, risk, primary_model, strategy='value'):
    """Fetch data, query LLMs, compute consensus. Returns analysis dict."""
    # ── Concurrent I/O fetch ─────────────────────────────────────────────────
    import yfinance as yf

    def _hist_dl():
        with _yf_semaphore:
            return yf.download(ticker, period='6mo', progress=False)['Close'].tail(30)

    with ThreadPoolExecutor(max_workers=9) as ex:
        f_hist       = ex.submit(_hist_dl)
        f_buffett    = ex.submit(get_buffett_metrics, ticker)
        f_realtime   = ex.submit(get_realtime_data, ticker)
        f_news       = ex.submit(get_recent_news, ticker)
        f_macro      = ex.submit(_fetch_fred_data)
        f_insiders   = ex.submit(fetch_insider_transactions, ticker, 60, 5, 5)
        f_multiframe = ex.submit(get_multiframe_signals, ticker)
        f_analyst    = ex.submit(get_analyst_consensus, ticker)
        f_tech       = ex.submit(get_tech_indicators, ticker) if risk == 'high' else None

    hist       = f_hist.result()
    buffett    = f_buffett.result()
    tech       = f_tech.result() if f_tech else {}
    realtime   = f_realtime.result()
    news       = f_news.result()
    macro      = f_macro.result()
    multiframe = f_multiframe.result()
    analyst    = f_analyst.result()
    try:
        insider_txns = f_insiders.result()
    except Exception:
        insider_txns = []

    # Sentiment depends on news — LLM call after concurrent fetch
    sentiment = analyze_news_sentiment(news, ticker, primary_model)

    # Live market block
    if realtime:
        sign = '+' if realtime['change_pct'] >= 0 else ''
        live_block = (
            f"LIVE Market Data (as of now):\n"
            f"  Price: ${realtime['price']:.2f}  |  "
            f"Today: {sign}{realtime['change_pct']}%  |  "
            f"Open: ${realtime['open']:.2f}  |  "
            f"High: ${realtime['high']:.2f}  |  "
            f"Low: ${realtime['low']:.2f}  |  "
            f"Volume: {realtime['volume']:,}"
        )
    else:
        live_block = "LIVE Market Data: unavailable"

    news_block = ""
    if news:
        news_lines = "\n".join(
            f"  {i+1}. [{item['published_at']}] {item['headline']}"
            for i, item in enumerate(news)
        )
        s_sign = '+' if sentiment['score'] >= 0 else ''
        news_block = (
            f"\nRecent News (use for sentiment context):\n{news_lines}\n"
            f"  News Sentiment: {sentiment['overall'].upper()} "
            f"(score: {s_sign}{sentiment['score']:.2f})"
        )

    macro_block = ""
    if macro:
        macro_block = (
            f"\nMacro context: Fed rate {macro.get('fed_rate', 'N/A')}%, "
            f"yield curve (10Y-2Y) {macro.get('yield_curve', 'N/A')}%, "
            f"CPI index {macro.get('cpi', 'N/A')}."
        )

    insiders_block = insider_prompt_block(insider_txns)

    multiframe_block = ""
    if multiframe:
        sma_pos = ('above' if multiframe.get('above_sma50') else
                   'below' if multiframe.get('above_sma50') is False else 'N/A')
        multiframe_block = (
            f"\nMulti-Timeframe Signals: "
            f"RSI(1d)={multiframe.get('rsi_1d', 'N/A')}, "
            f"RSI(1w)={multiframe.get('rsi_1w', 'N/A')}, "
            f"Monthly trend={multiframe.get('trend_1mo', 'N/A')}, "
            f"Price {sma_pos} 50-day SMA."
        )

    analyst_block = ""
    if analyst:
        sign = '+' if (analyst.get('upside_pct') or 0) >= 0 else ''
        analyst_block = (
            f"\nWall Street Analysts ({analyst.get('num_analysts', '?')} covering): "
            f"Consensus={analyst.get('rating_key', 'N/A')}, "
            f"Target=${analyst.get('target_mean', 'N/A')} "
            f"(range ${analyst.get('target_low', '?')}–${analyst.get('target_high', '?')}), "
            f"Implied upside: {sign}{analyst.get('upside_pct', 'N/A')}%."
        )

    strategy_guidance = STRATEGY_PROMPTS.get(strategy, STRATEGY_PROMPTS['value'])

    prompt = f"""
    Buffett Trading AI for {ticker} | Risk: {risk} | Strategy: {strategy.upper()}
    Buffett Score: {buffett['score']}/100 | ROE: {buffett.get('roe','?')}% | ROIC: {buffett.get('roic','?')}% | Debt/Eq: {buffett.get('debt_eq','?')} | OpMargin: {buffett.get('op_margin','?')}% | FCF Yield: {buffett.get('fcf_yield','?')}% | P/E: {buffett.get('pe','?')} | P/B: {buffett.get('pb','?')}
    {live_block}{news_block}{macro_block}{insiders_block}{multiframe_block}{analyst_block}
    Recent Prices (30d): {hist.to_dict()}
    Tech {'(RSI: ' + str(tech.get('rsi', 'N/A')) + ', MACD: ' + str(tech.get('macd', 'N/A')) + ')' if tech else ''}

    Strategy Guidance: {strategy_guidance}

    Additional rules:
    - Risk mgmt: Position <2% portfolio, stop-loss 3-7%
    - Factor in the LIVE price, news sentiment, and macro context above

    JSON only: {{"action": "BUY|SELL|HOLD", "confidence": 0.85, "qty": 10, "reason": "2 sentences", "stop_pct": 0.05}}
    """

    models_to_query = [primary_model]
    if primary_model != MODELS[1]:
        models_to_query.append(MODELS[1])

    def _query_one(model):
        try:
            resp = ollama.chat(model=model, messages=[{'role': 'user', 'content': prompt}],
                               options={'temperature': 0.2})
            advice_str = resp['message']['content'].strip()
            try:
                advice = json.loads(advice_str) if advice_str.startswith('{') else {'reason': advice_str}
            except json.JSONDecodeError:
                advice = {'error': 'Invalid JSON', 'raw': advice_str}
            return model, advice
        except Exception as e:
            return model, {'error': str(e)}

    console.print(f"[dim]Querying {len(models_to_query)} model(s) concurrently...[/dim]")
    responses = {}
    with ThreadPoolExecutor(max_workers=len(models_to_query)) as ex:
        for model, advice in ex.map(lambda m: _query_one(m), models_to_query):
            responses[model] = advice

    actions = [r.get('action', 'HOLD') for r in responses.values() if isinstance(r, dict) and 'action' in r]
    consensus = max(set(actions), key=actions.count) if actions else 'HOLD'

    best_buy_resp = None
    if consensus == 'BUY':
        best_buy_resp = max(
            (r for r in responses.values() if isinstance(r, dict) and r.get('action') == 'BUY'),
            key=lambda x: x.get('confidence', 0),
            default=None,
        )
        log_recommendation(
            ticker=ticker,
            action='BUY',
            confidence=best_buy_resp.get('confidence', 0.0),
            qty=best_buy_resp.get('qty', 0),
            entry_price=realtime.get('price', 0.0),
            reason=best_buy_resp.get('reason', ''),
            model=primary_model,
            strategy=strategy,
            buffett_score=buffett.get('score', 0),
        )

    return {
        'buffett': buffett,
        'tech': tech,
        'realtime': realtime,
        'news': news,
        'sentiment': sentiment,
        'responses': responses,
        'consensus': consensus,
        'best_buy_resp': best_buy_resp,
        'analyst': analyst,
    }


def _place_order(ticker, best_resp):
    """Submit a paper market BUY order using the best AI response."""
    try:
        order = MarketOrderRequest(
            symbol=ticker,
            qty=int(best_resp.get('qty', 1)),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        result = trading_client.submit_order(order)
        console.print(f"[bold green]Order submitted:[/bold green] {result.id}")
    except Exception as e:
        console.print(f"[red]Order error: {e}[/red]")
