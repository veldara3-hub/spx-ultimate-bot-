import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import json
import time
import warnings
from datetime import datetime, timedelta, timezone
warnings.filterwarnings('ignore')

# --- إعدادات البوت ---
# ملاحظة: تأكد من وضع التوكن في GitHub Secrets وليس هنا مباشرة إذا كان المستودع عاماً
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'ضع_التوكن_هنا_للتجربة_المحلية_فقط')
CHAT_ID = os.environ.get('CHAT_ID', 'ضع_الآيدي_هنا_للتجربة_المحلية_فقط')

# ملفات البيانات
PORTFOLIO_FILE = 'options_portfolio.json'
SETTINGS_FILE = 'spx_settings.json'
PROCESSED_FILE = 'processed_spx.json'
BRAIN_FILE = 'bot_brain.json'
SIGNALS_FILE = 'signals_history.json'
NEWS_CACHE = 'news_cache.json'
PROMISING_OPTIONS_FILE = 'promising_options.json'
ALERTS_LOG = 'alerts_log.json'

_indicators_cache = {'data': None, 'timestamp': 0}
_CACHE_DURATION = 300

def get_saudi_time():
    utc_now = datetime.now(timezone.utc)
    saudi_tz = timezone(timedelta(hours=3))
    saudi_now = utc_now.astimezone(saudi_tz)
    return {
        'full': saudi_now.strftime('%Y-%m-%d %H:%M:%S'),
        'date': saudi_now.strftime('%Y-%m-%d'),
        'time': saudi_now.strftime('%H:%M'),
        'time_with_seconds': saudi_now.strftime('%H:%M:%S'),
        'hour': saudi_now.hour, 'minute': saudi_now.minute,
        'weekday': saudi_now.weekday(),
        'weekday_name': ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد'][saudi_now.weekday()]
    }

def get_option_full_info(strike, opt_type, exp_date):
    try:
        ticker = yf.Ticker('SPY')
        chain = ticker.option_chain(exp_date)
        contract = chain.calls[chain.calls['strike'] == strike] if opt_type == 'CALL' else chain.puts[chain.puts['strike'] == strike]
        if contract.empty: return None
        row = contract.iloc[0]
        exp_formatted = datetime.strptime(exp_date, '%Y-%m-%d').strftime('%y%m%d')
        type_letter = 'C' if opt_type == 'CALL' else 'P'
        strike_formatted = f"{int(strike * 1000):08d}"
        option_symbol = f"SPY{exp_formatted}{type_letter}{strike_formatted}"
        exp_datetime = datetime.strptime(exp_date, '%Y-%m-%d')
        dte = (exp_datetime - datetime.now()).days
        last_trade = row.get('lastDate', None)
        last_trade_str = last_trade.strftime('%Y-%m-%d %H:%M') if last_trade and hasattr(last_trade, 'strftime') else "لا يوجد"
        bid, ask = row.get('bid', 0), row.get('ask', 0)
        return {
            'symbol': option_symbol, 'dte': dte, 'dte_text': f"{dte} يوم",
            'open_interest': int(row.get('openInterest', 0)), 'last_trade': last_trade_str,
            'bid': round(float(bid), 2), 'ask': round(float(ask), 2),
            'spread': round(float(ask - bid), 2) if ask > 0 else 0,
            'in_the_money': row.get('inTheMoney', False)
        }
    except: return None

def load_json(fn, default=None):
    if default is None: default = {}
    try:
        with open(fn, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

def save_json(fn, data):
    with open(fn, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)

def get_settings():
    defaults = {'capital': 10000, 'risk_percent': 2.0, 'last_daily_report': None, 'last_results_check': None, 'last_weekly_stats': None, 'last_morning_report': None, 'last_pre_close': None}
    settings = load_json(SETTINGS_FILE, defaults)
    for key in defaults:
        if key not in settings: settings[key] = defaults[key]
    return settings

def get_brain():
    defaults = {
        'rsi_buy_threshold': 30, 'rsi_sell_threshold': 70, 'vix_fear_level': 25,
        'total_predictions': 0, 'correct_predictions': 0, 'wrong_predictions': 0,
        'last_adjustment_reason': 'لم يبدأ التعلم بعد', 'last_prediction_sent': None,
        'mistake_patterns': {'rsi_too_early': 0, 'vix_ignored': 0, 'macd_false_signal': 0},
        'learning_history': [], 'best_strategy': 'CALL', 'avoid_patterns': [],
        'accuracy_stats': {'total': 0, 'correct': 0, 'wrong': 0, 'by_hour': {}, 'by_strategy': {}},
        'option_success_rate': {'calls': 0, 'puts': 0, 'total_tracked': 0}
    }
    brain = load_json(BRAIN_FILE, defaults)
    for key in defaults:
        if key not in brain: brain[key] = defaults[key]
    return brain

def save_brain(brain): save_json(BRAIN_FILE, brain)

def find_promising_cheap_options():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options: return None, "❌ لا توجد بيانات."
        ind = calculate_indicators()
        if not ind: return None, "❌ لا يمكن جلب المؤشرات."
        brain = get_brain()
        expirations = ticker.options[:3] if len(ticker.options) >= 3 else ticker.options
        promising_calls, promising_puts = [], []
        current_price = ind['price']
        
        for exp in expirations:
            chain = ticker.option_chain(exp)
            for _, row in chain.calls.iterrows():
                if row['lastPrice'] < 2.5 and row['lastPrice'] > 0.15:
                    distance = ((row['strike'] - current_price) / current_price) * 100
                    if 0 < distance < 4:
                        confidence = 0
                        if ind['rsi'] < brain['rsi_buy_threshold']: confidence += 30
                        if ind['macd'] > ind['macd_signal']: confidence += 25
                        if ind['vix'] < 20: confidence += 20
                        if row['volume'] > 100: confidence += 15
                        if row['delta'] > 0.3: confidence += 10
                        if confidence >= 40:
                            option_info = get_option_full_info(row['strike'], 'CALL', exp)
                            promising_calls.append({
                                'type': 'CALL', 'strike': row['strike'], 'price': row['lastPrice'], 'exp': exp,
                                'volume': int(row['volume']), 'iv': row['impliedVolatility'] * 100, 'delta': row['delta'],
                                'distance_pct': round(distance, 2), 'confidence': confidence,
                                'reason': 'RSI منخفض + MACD إيجابي' if ind['rsi'] < 35 and ind['macd'] > ind['macd_signal'] else 'ظروف سوقية مناسبة',
                                'option_symbol': option_info['symbol'] if option_info else 'N/A',
                                'dte_text': option_info['dte_text'] if option_info else 'N/A',
                                'open_interest': option_info['open_interest'] if option_info else 0,
                                'bid': option_info['bid'] if option_info else 0, 'ask': option_info['ask'] if option_info else 0,
                                'spread': option_info['spread'] if option_info else 0,
                                'in_the_money': option_info['in_the_money'] if option_info else False
                            })
            for _, row in chain.puts.iterrows():
                if row['lastPrice'] < 2.5 and row['lastPrice'] > 0.15:
                    distance = ((current_price - row['strike']) / current_price) * 100
                    if 0 < distance < 4:
                        confidence = 0
                        if ind['rsi'] > brain['rsi_sell_threshold']: confidence += 30
                        if ind['macd'] < ind['macd_signal']: confidence += 25
                        if ind['vix'] > 20: confidence += 20
                        if row['volume'] > 100: confidence += 15
                        if abs(row['delta']) > 0.3: confidence += 10
                        if confidence >= 40:
                            option_info = get_option_full_info(row['strike'], 'PUT', exp)
                            promising_puts.append({
                                'type': 'PUT', 'strike': row['strike'], 'price': row['lastPrice'], 'exp': exp,
                                'volume': int(row['volume']), 'iv': row['impliedVolatility'] * 100, 'delta': row['delta'],
                                'distance_pct': round(distance, 2), 'confidence': confidence,
                                'reason': 'RSI مرتفع + MACD سلبي' if ind['rsi'] > 65 and ind['macd'] < ind['macd_signal'] else 'ظروف سوقية مناسبة',
                                'option_symbol': option_info['symbol'] if option_info else 'N/A',
                                'dte_text': option_info['dte_text'] if option_info else 'N/A',
                                'open_interest': option_info['open_interest'] if option_info else 0,
                                'bid': option_info['bid'] if option_info else 0, 'ask': option_info['ask'] if option_info else 0,
                                'spread': option_info['spread'] if option_info else 0,
                                'in_the_money': option_info['in_the_money'] if option_info else False
                            })
        
        promising_calls.sort(key=lambda x: x['confidence'], reverse=True)
        promising_puts.sort(key=lambda x: x['confidence'], reverse=True)
        options_data = {'timestamp': datetime.now(timezone.utc).isoformat(), 'current_price': current_price, 'calls': promising_calls[:5], 'puts': promising_puts[:5], 'status': 'pending'}
        history = load_json(PROMISING_OPTIONS_FILE, {'options': []})
        history['options'].append(options_data)
        if len(history['options']) > 100: history['options'] = history['options'][-100:]
        save_json(PROMISING_OPTIONS_FILE, history)
        return options_data, None
    except Exception as e: return None, f"❌ خطأ: {str(e)}"

def track_option_performance():
    history = load_json(PROMISING_OPTIONS_FILE, {'options': []})
    brain = get_brain()
    updated = False
    for batch in history['options']:
        if batch.get('status') == 'tracked': continue
        batch_time = datetime.fromisoformat(batch['timestamp'])
        hours_since = (datetime.now(timezone.utc) - batch_time).total_seconds() / 3600
        if hours_since < 24: continue
        try:
            current_data = yf.Ticker('SPY').history(period='1d')
            if len(current_data) < 1: continue
            current_price = float(current_data['Close'].iloc[-1])
            predicted_price = batch['current_price']
            change_pct = ((current_price - predicted_price) / predicted_price) * 100
            calls_correct = sum(1 for _ in batch.get('calls', []) if change_pct > 0.5)
            puts_correct = sum(1 for _ in batch.get('puts', []) if change_pct < -0.5)
            batch['status'] = 'tracked'
            batch['actual_change'] = round(change_pct, 2)
            brain['option_success_rate']['total_tracked'] += len(batch.get('calls', [])) + len(batch.get('puts', []))
            brain['option_success_rate']['calls'] += calls_correct
            brain['option_success_rate']['puts'] += puts_correct
            updated = True
        except: continue
    if updated:
        save_json(PROMISING_OPTIONS_FILE, history)
        save_brain(brain)
    return updated

def calculate_indicators():
    global _indicators_cache
    now = time.time()
    if _indicators_cache['data'] and (now - _indicators_cache['timestamp']) < _CACHE_DURATION:
        return _indicators_cache['data']
    try:
        data = yf.Ticker('SPY').history(period='6mo', interval='1d')
        if len(data) < 50: return None
        close, high, low, volume = data['Close'], data['High'], data['Low'], data['Volume']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = float(100 - (100 / (1 + (gain / loss))).iloc[-1])
        ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
        macd, macd_signal = float(ema12.iloc[-1] - ema26.iloc[-1]), float((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1])
        sma_20, sma_50 = float(close.rolling(20).mean().iloc[-1]), float(close.rolling(50).mean().iloc[-1])
        bb_std = close.rolling(20).std().iloc[-1]
        bb_upper, bb_lower = sma_20 + (bb_std * 2), sma_20 - (bb_std * 2)
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1
        vix_data = yf.Ticker('^VIX').history(period='5d')
        result = {
            'rsi': round(rsi, 2), 'macd': round(macd, 4), 'macd_signal': round(macd_signal, 4),
            'sma_20': round(sma_20, 2), 'sma_50': round(sma_50, 2), 'bb_upper': round(bb_upper, 2),
            'bb_lower': round(bb_lower, 2), 'volume_ratio': round(vol_ratio, 2),
            'price': round(float(close.iloc[-1]), 2), 'vix': round(float(vix_data['Close'].iloc[-1]), 2)
        }
        _indicators_cache = {'data': result, 'timestamp': now}
        return result
    except: return None

def calculate_support_resistance():
    try:
        data = yf.Ticker('SPY').history(period='3mo', interval='1d')
        high, low, close = data['High'], data['Low'], data['Close']
        res, sup = [], []
        for i in range(5, len(data) - 5):
            if high.iloc[i] == high.iloc[i-5:i+6].max(): res.append(round(float(high.iloc[i]), 2))
            if low.iloc[i] == low.iloc[i-5:i+6].min(): sup.append(round(float(low.iloc[i]), 2))
        return {'resistance': sorted(list(set(res)))[-3:], 'support': sorted(list(set(sup)))[:3], 'current_price': round(float(close.iloc[-1]), 2)}
    except: return None

def analyze_news_sentiment():
    try:
        cache = load_json(NEWS_CACHE, {'last_update': None, 'sentiment': 'neutral', 'articles': []})
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if cache.get('last_update') == today: return cache['sentiment'], cache['articles'][:3]
        news = yf.Ticker('SPY').news[:10] if yf.Ticker('SPY').news else []
        pos_words = ['up', 'rise', 'gain', 'bull', 'rally', 'growth', 'positive', 'strong']
        neg_words = ['down', 'fall', 'drop', 'bear', 'crash', 'decline', 'negative', 'weak', 'fear']
        score, articles = 0, []
        for item in news:
            title = item.get('title', '').lower()
            if title:
                score += sum(1 for w in pos_words if w in title) - sum(1 for w in neg_words if w in title)
                articles.append({'title': item.get('title', ''), 'publisher': item.get('publisher', '')})
        sentiment = 'positive' if score > 2 else 'negative' if score < -2 else 'neutral'
        save_json(NEWS_CACHE, {'last_update': today, 'sentiment': sentiment, 'articles': articles[:5], 'score': score})
        return sentiment, articles[:3]
    except: return 'neutral', []

def detect_unusual_activity():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options: return None
        chain = ticker.option_chain(ticker.options[0])
        calls, puts = chain.calls, chain.puts
        avg_c, avg_p = calls['volume'].mean(), puts['volume'].mean()
        alerts = []
        for _, row in calls[calls['volume'] > avg_c * 3].head(2).iterrows():
            alerts.append({'type': 'CALL', 'strike': row['strike'], 'volume': int(row['volume']), 'ratio': round(row['volume']/avg_c, 1)})
        for _, row in puts[puts['volume'] > avg_p * 3].head(2).iterrows():
            alerts.append({'type': 'PUT', 'strike': row['strike'], 'volume': int(row['volume']), 'ratio': round(row['volume']/avg_p, 1)})
        return alerts[:3] if alerts else None
    except: return None

def suggest_strategy(ind, brain):
    if ind['vix'] > 25 and abs(ind['rsi'] - 50) < 20: return {'name': 'Iron Condor', 'desc': 'سوق متذبذب مع خوف مرتفع', 'action': 'بيع Call و Put بعيداً عن السعر'}
    if ind['rsi'] < 40 and ind['macd'] > ind['macd_signal'] and ind['vix'] < 20: return {'name': 'Bull Call Spread', 'desc': 'اتجاه صاعد مع زخم إيجابي', 'action': 'شراء Call عند السعر + بيع Call أعلى'}
    if ind['rsi'] > 60 and ind['macd'] < ind['macd_signal']: return {'name': 'Bear Put Spread', 'desc': 'اتجاه هابط مع تشبع شرائي', 'action': 'شراء Put عند السعر + بيع Put أدنى'}
    if ind['rsi'] < 25 and ind['vix'] < 15: return {'name': 'Long Call', 'desc': 'فرصة شراء قوية مع خوف منخفض', 'action': 'شراء Call مباشرة'}
    if ind['rsi'] > 75 and ind['vix'] > 20: return {'name': 'Long Put', 'desc': 'فرصة بيع قوية مع خوف مرتفع', 'action': 'شراء Put مباشرة'}
    return {'name': 'انتظار', 'desc': 'السوق غير واضح', 'action': 'انتظر إشارة أوضح'}

def calculate_live_pnl():
    data = load_json(PORTFOLIO_FILE, {'positions': []})
    if not data['positions']: return None
    results, total_pnl = [], 0
    for pos in data['positions']:
        try:
            spy = float(yf.Ticker('SPY').history(period='1d')['Close'].iloc[-1])
            intrinsic = max(0, spy - pos['strike']) if pos['type'] == 'CALL' else max(0, pos['strike'] - spy)
            current_opt = max(0.01, intrinsic * 0.8)
            pnl = (current_opt - pos['entry']) * 100 * pos['qty']
            total_pnl += pnl
            results.append({'type': pos['type'], 'strike': pos['strike'], 'entry': pos['entry'], 'current': round(current_opt, 2), 'pnl': round(pnl, 2), 'pnl_pct': round(((current_opt - pos['entry']) / pos['entry']) * 100, 1), 'qty': pos['qty']})
        except: continue
    return {'positions': results, 'total_pnl': round(total_pnl, 2)}

def calculate_what_if(target_price):
    data = load_json(PORTFOLIO_FILE, {'positions': []})
    if not data['positions']: return "❌ المحفظة فارغة."
    msg = f"🔮 <b>سيناريو 'ماذا لو' وصل SPY إلى ${target_price}</b>\n\n"
    total_pnl = 0
    for pos in data['positions']:
        intrinsic = max(0, target_price - pos['strike']) if pos['type'] == 'CALL' else max(0, pos['strike'] - target_price)
        current_opt = max(0.01, intrinsic * 0.8)
        pnl = (current_opt - pos['entry']) * 100 * pos['qty']
        total_pnl += pnl
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg += f"{emoji} <b>{pos['type']} {pos['strike']}</b>: {pnl:+.2f}$ ({((current_opt - pos['entry']) / pos['entry'])*100:+.1f}%)\n"
    msg += f"\n💰 <b>الإجمالي:</b> {total_pnl:+.2f}$"
    return msg

def generate_morning_report():
    ind = calculate_indicators()
    if not ind: return None
    brain = get_brain()
    options_data, error = find_promising_cheap_options()
    if error: return error
    if not options_data: return "❌ لا توجد عقود واعدة حالياً."
    
    saudi_time = get_saudi_time()
    levels = calculate_support_resistance()
    sentiment, articles = analyze_news_sentiment()
    strategy = suggest_strategy(ind, brain)
    
    msg = f"🌅 <b>تقرير الصباح الشامل والتلقائي</b>\n"
    msg += f"📅 {saudi_time['weekday']} {saudi_time['date']} | 🕐 {saudi_time['time_with_seconds']}\n\n"
    
    msg += f"<b>📊 حالة السوق والتحليل:</b>\n"
    msg += f"💰 SPY: ${ind['price']} | 📈 RSI: {ind['rsi']} | 😱 VIX: {ind['vix']}\n"
    msg += f"📉 MACD: {'إيجابي ✅' if ind['macd'] > ind['macd_signal'] else 'سلبي ❌'}\n"
    msg += f"📰 <b>مشاعر الأخبار:</b> {sentiment.upper()} {'🟢' if sentiment=='positive' else '🔴' if sentiment=='negative' else '🟡'}\n"
    if articles: msg += f"• {articles[0]['title'][:50]}...\n"
    
    msg += f"\n🗺️ <b>الدعم والمقاومة:</b>\n"
    if levels:
        msg += f"🔴 مقاومة: {', '.join([f'${r}' for r in levels['resistance']])}\n"
        msg += f"🟢 دعم: {', '.join([f'${s}' for s in levels['support']])}\n"
    
    msg += f"\n🧠 <b>الاستراتيجية المقترحة:</b> {strategy['name']}\n"
    msg += f"📝 {strategy['desc']}\n⚡ {strategy['action']}\n\n"
    
    if options_data['calls']:
        msg += f"🟢 <b>أفضل 5 عقود CALL واعدة:</b>\n\n"
        for i, c in enumerate(options_data['calls'][:5], 1):
            potential = ((c['strike'] - ind['price']) / c['price']) * 100 if c['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${c['strike']}</b> | 💵 ${c['price']} | 📅 {c['exp']}\n"
            msg += f"   🆔 <code>{c.get('option_symbol', 'N/A')}</code> | ⏳ {c.get('dte_text', 'N/A')}\n"
            msg += f"   💹 Bid/Ask: ${c.get('bid', 0)}/${c.get('ask', 0)} | OI: {c.get('open_interest', 0)}\n"
            msg += f"   🎯 الثقة: {c['confidence']}% | IV: {c['iv']:.1f}% | Delta: {c['delta']:.2f}\n"
            msg += f"   💰 العائد: +{potential:.0f}% | 🧠 {c['reason']}\n\n"
    
    if options_data['puts']:
        msg += f"🔴 <b>أفضل 5 عقود PUT واعدة:</b>\n\n"
        for i, p in enumerate(options_data['puts'][:5], 1):
            potential = ((ind['price'] - p['strike']) / p['price']) * 100 if p['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${p['strike']}</b> | 💵 ${p['price']} | 📅 {p['exp']}\n"
            msg += f"   🆔 <code>{p.get('option_symbol', 'N/A')}</code> | ⏳ {p.get('dte_text', 'N/A')}\n"
            msg += f"   💹 Bid/Ask: ${p.get('bid', 0)}/${p.get('ask', 0)} | OI: {p.get('open_interest', 0)}\n"
            msg += f"   🎯 الثقة: {p['confidence']}% | IV: {p['iv']:.1f}% | Delta: {p['delta']:.2f}\n"
            msg += f"   💰 العائد: +{potential:.0f}% | 🧠 {p['reason']}\n\n"
            
    msg += f"⚠️ <b>تنبيه:</b> استخدم إدارة رأس مال صارمة."
    return msg

def get_periodic_options_report():
    ind = calculate_indicators()
    if not ind: return None
    brain = get_brain()
    options_data, error = find_promising_cheap_options()
    if error or not options_data: return None
    saudi_time = get_saudi_time()
    levels = calculate_support_resistance()
    
    msg = f"💎 <b>عقود واعدة الآن</b>\n"
    msg += f"📅 {saudi_time['weekday']} {saudi_time['date']} | 🕐 {saudi_time['time']} (السعودية)\n"
    msg += f"💰 SPY: ${ind['price']} | 📈 RSI: {ind['rsi']} | 😱 VIX: {ind['vix']}\n\n"
    
    msg += f"🗺️ <b>المستويات:</b>\n"
    if levels:
        msg += f"🔴 مقاومة: {', '.join([f'${r}' for r in levels['resistance']])}\n"
        msg += f"🟢 دعم: {', '.join([f'${s}' for s in levels['support']])}\n\n"
    
    if options_data['calls']:
        msg += f"🟢 <b>أفضل 3 CALLs واعدة:</b>\n\n"
        for i, c in enumerate(options_data['calls'][:3], 1):
            potential = ((c['strike'] - ind['price']) / c['price']) * 100 if c['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${c['strike']}</b> | 💵 ${c['price']} | 📅 {c['exp']}\n"
            msg += f"   🆔 <code>{c.get('option_symbol', 'N/A')}</code> | ⏳ {c.get('dte_text', 'N/A')}\n"
            msg += f"   💹 Bid/Ask: ${c.get('bid', 0)}/${c.get('ask', 0)} | OI: {c.get('open_interest', 0)}\n"
            msg += f"   🎯 الثقة: {c['confidence']}% | IV: {c['iv']:.1f}% | Delta: {c['delta']:.2f}\n"
            msg += f"   💰 العائد: +{potential:.0f}% | 🧠 {c['reason']}\n\n"
    
    if options_data['puts']:
        msg += f"🔴 <b>أفضل 3 PUTs واعدة:</b>\n\n"
        for i, p in enumerate(options_data['puts'][:3], 1):
            potential = ((ind['price'] - p['strike']) / p['price']) * 100 if p['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${p['strike']}</b> | 💵 ${p['price']} | 📅 {p['exp']}\n"
            msg += f"   🆔 <code>{p.get('option_symbol', 'N/A')}</code> | ⏳ {p.get('dte_text', 'N/A')}\n"
            msg += f"   💹 Bid/Ask: ${p.get('bid', 0)}/${p.get('ask', 0)} | OI: {p.get('open_interest', 0)}\n"
            msg += f"   🎯 الثقة: {p['confidence']}% | IV: {p['iv']:.1f}% | Delta: {p['delta']:.2f}\n"
            msg += f"   💰 العائد: +{potential:.0f}% | 🧠 {p['reason']}\n\n"
    
    msg += f"\n⚠️ <b>تنبيه:</b> استخدم إدارة رأس مال صارمة."
    return msg

def check_prediction_results():
    history = load_json(SIGNALS_FILE, {'signals': []})
    brain = get_brain()
    pending_signals = [s for s in history['signals'] if s['status'] == 'pending']
    results = []
    for signal in pending_signals:
        try:
            signal_time = datetime.fromisoformat(signal['timestamp'])
            hours_since = (datetime.now(timezone.utc) - signal_time).total_seconds() / 3600
            if hours_since < 24: continue
            current_data = yf.Ticker('SPY').history(period='1d')
            if len(current_data) < 1: continue
            current_price = float(current_data['Close'].iloc[-1])
            predicted_price = signal['price']
            change_pct = ((current_price - predicted_price) / predicted_price) * 100
            is_correct = False
            if 'CALL' in signal['decision'] and change_pct > 0.5: is_correct = True
            elif 'PUT' in signal['decision'] and change_pct < -0.5: is_correct = True
            signal['status'] = 'correct' if is_correct else 'wrong'
            signal['actual_change'] = round(change_pct, 2)
            if not is_correct:
                brain['wrong_predictions'] += 1
            else:
                brain['correct_predictions'] += 1
            brain['total_predictions'] += 1
            results.append(signal)
        except: continue
    if results:
        save_json(SIGNALS_FILE, history)
        save_brain(brain)
    return results

def deep_learn_from_mistakes():
    history = load_json(SIGNALS_FILE, {'signals': []})
    brain = get_brain()
    updated, lessons = False, []
    recent_signals = [s for s in history['signals'] if s['status'] in ['correct', 'wrong']][-20:]
    if len(recent_signals) < 5: return False, []
    wrong_calls = [s for s in recent_signals if s['status'] == 'wrong' and 'CALL' in s.get('decision', '')]
    wrong_puts = [s for s in recent_signals if s['status'] == 'wrong' and 'PUT' in s.get('decision', '')]
    if len(wrong_calls) > 3:
        brain['rsi_buy_threshold'] = max(15, brain['rsi_buy_threshold'] - 3)
        lessons.append(f"📉 أخطأت {len(wrong_calls)} مرات في CALLs. RSI → {brain['rsi_buy_threshold']}")
        updated = True
    if len(wrong_puts) > 3:
        brain['rsi_sell_threshold'] = min(85, brain['rsi_sell_threshold'] + 3)
        lessons.append(f"📈 أخطأت {len(wrong_puts)} مرات في PUTs. RSI → {brain['rsi_sell_threshold']}")
        updated = True
    if lessons:
        brain['learning_history'].append({'date': datetime.now(timezone.utc).isoformat(), 'lessons': lessons})
        if len(brain['learning_history']) > 50: brain['learning_history'] = brain['learning_history'][-50:]
        save_brain(brain)
    return updated, lessons

def generate_prediction():
    ind = calculate_indicators()
    if not ind: return None
    brain, levels = get_brain(), calculate_support_resistance()
    sentiment, articles = analyze_news_sentiment()
    strategy = suggest_strategy(ind, brain)
    saudi_time = get_saudi_time()
    reasons, score = [], 0
    if ind['rsi'] < brain['rsi_buy_threshold']: score += 2; reasons.append(f"📈 RSI منخفض ({ind['rsi']})")
    elif ind['rsi'] > brain['rsi_sell_threshold']: score -= 2; reasons.append(f"📉 RSI مرتفع ({ind['rsi']})")
    if ind['macd'] > ind['macd_signal']: score += 1; reasons.append("✅ MACD إيجابي")
    else: score -= 1; reasons.append("❌ MACD سلبي")
    if ind['vix'] > brain['vix_fear_level']: score -= 2; reasons.append(f"⚠️ VIX مرتفع ({ind['vix']})")
    else: score += 1; reasons.append(f"🟢 VIX مستقر ({ind['vix']})")
    if ind['price'] > ind['sma_20']: score += 1; reasons.append(f"📊 السعر فوق SMA20")
    if sentiment == 'positive': score += 1; reasons.append("📰 أخبار إيجابية")
    elif sentiment == 'negative': score -= 1; reasons.append("📰 أخبار سلبية")
    
    if score >= 3: decision, advice = "🟢 شراء CALL", "💡 ادخل بـ 50% من حجمك وضع وقف خسارة -20%"
    elif score <= -3: decision, advice = "🔴 شراء PUT", "💡 ادخل بحذر وضع هدف ربح +30%"
    elif score >= 1: decision, advice = "🟡 CALL بحذر", "⚠️ انتظر تأكيداً إضافياً"
    elif score <= -1: decision, advice = "🟠 PUT بحذر", "⚠️ انتظر تأكيداً إضافياً"
    else: decision, advice = "⛔ لا تدخل", "💡 حافظ على رأس المال"
    
    signal = {'timestamp': datetime.now(timezone.utc).isoformat(), 'price': ind['price'], 'decision': decision, 'score': score, 'reasons': reasons, 'indicators': ind, 'strategy': strategy['name'], 'status': 'pending'}
    history = load_json(SIGNALS_FILE, {'signals': []})
    history['signals'].append(signal)
    save_json(SIGNALS_FILE, history)
    brain['last_prediction_sent'] = datetime.now(timezone.utc).isoformat()
    save_brain(brain)
    
    msg = f"🤖 <b>توقع البوت التلقائي</b>\n"
    msg += f"📅 {saudi_time['weekday']} {saudi_time['date']} | 🕐 {saudi_time['time']} السعودية\n\n"
    msg += f"🎯 <b>القرار:</b> {decision}\n💰 <b>السعر:</b> ${ind['price']}\n🧠 <b>الاستراتيجية:</b> {strategy['name']}\n\n"
    msg += f"<b>الأسباب:</b>\n" + "\n".join([f"• {r}" for r in reasons]) + f"\n\n{advice}\n"
    if levels: msg += f"🗺️ <b>المستويات:</b>\n• مقاومة: {', '.join([f'${r}' for r in levels['resistance']])}\n• دعم: {', '.join([f'${s}' for s in levels['support']])}\n"
    if articles: msg += f"📰 <b>الأخبار:</b>\n" + "\n".join([f"• {art['title'][:50]}..." for art in articles[:2]]) + "\n"
    msg += f"🎯 <b>دقة البوت:</b> {(brain['correct_predictions']/max(1, brain['total_predictions']))*100:.1f}%"
    return msg

def handle_smart_chat(text, ind, brain):
    text = text.lower().strip()
    if any(w in text for w in ['كيف السوق', 'وضع السوق', 'السوق اليوم']):
        if not ind: return "❌ لا يمكن جلب البيانات."
        trend = "صاعد 🟢" if ind['price'] > ind['sma_20'] else "هابط 🔴"
        return f"📊 <b>ملخص سريع:</b>\nالاتجاه: {trend}\nVIX: {ind['vix']}\nRSI: {ind['rsi']}\n\n{'🟢 السوق جيد للفرص' if ind['vix'] < 25 else '⚠️ أنصح بالانتظار'}."
    if any(w in text for w in ['اشتري', 'هل ادخل', 'ادخل السوق']):
        if not ind: return "❌"
        if ind['rsi'] < brain['rsi_buy_threshold'] and ind['vix'] < brain['vix_fear_level']:
            return "✅ <b>نعم، المؤشرات إيجابية.</b>\nادخل بـ 50% من حجمك مع وقف خسارة."
        else:
            return "⚠️ <b>لا أنصح بالدخول الآن.</b>\nانتظر إشارة أوضح."
    if any(w in text for w in ['فيه خطر', 'مخاطرة']):
        if not ind: return "❌"
        if ind['vix'] > 25: return f"🚨 <b>مخاطرة عالية.</b>\nVIX: {ind['vix']}. قلل حجم العقود."
        else: return f"🟢 <b>مخاطرة منخفضة.</b>\nVIX: {ind['vix']}."
    return None

def check_auto_alerts(ind, levels, whales):
    saudi_time = get_saudi_time()
    alerts_log = load_json(ALERTS_LOG, {'alerts': []})
    today = saudi_time['date']
    new_alerts = []
    
    if ind['vix'] > 30:
        alert_key = f"{today}_vix_high"
        if not any(a['key'] == alert_key for a in alerts_log['alerts']):
            new_alerts.append(f"🚨 <b>تنبيه VIX مرتفع جداً!</b>\n🕐 {saudi_time['time']} السعودية\n\nمؤشر الخوف VIX وصل إلى {ind['vix']}!\n\n⚠️ هذا يعني:\n• تقلبات حادة في السوق\n• مخاطرة عالية جداً\n• أنصح بتقليل حجم الصفقات")
            alerts_log['alerts'].append({'key': alert_key, 'type': 'vix_high', 'time': saudi_time['full']})
    
    if levels and levels['resistance'] and ind['price'] > levels['resistance'][0]:
        alert_key = f"{today}_breakout_resistance"
        if not any(a['key'] == alert_key for a in alerts_log['alerts']):
            new_alerts.append(f"🔥 <b>اختراق مقاومة!</b>\n🕐 {saudi_time['time']} السعودية\n\n💰 السعر: ${ind['price']}\n🔴 المقاومة: ${levels['resistance'][0]}\n\n✅ إشارة صعود قوية!")
            alerts_log['alerts'].append({'key': alert_key, 'type': 'breakout', 'time': saudi_time['full']})
    
    if levels and levels['support'] and ind['price'] < levels['support'][0]:
        alert_key = f"{today}_breakdown_support"
        if not any(a['key'] == alert_key for a in alerts_log['alerts']):
            new_alerts.append(f"⚠️ <b>كسر دعم!</b>\n🕐 {saudi_time['time']} السعودية\n\n💰 السعر: ${ind['price']}\n🟢 الدعم: ${levels['support'][0]}\n\n🔴 إشارة هبوط!")
            alerts_log['alerts'].append({'key': alert_key, 'type': 'breakdown', 'time': saudi_time['full']})
            
    if ind['price'] > ind['bb_upper']:
        alert_key = f"{today}_bb_upper"
        if not any(a['key'] == alert_key for a in alerts_log['alerts']):
            new_alerts.append(f"📈 <b>اختراق Bollinger Band العلوي!</b>\n🕐 {saudi_time['time']} السعودية\n\n💰 السعر: ${ind['price']} تجاوز ${ind['bb_upper']}\n⚠️ السوق في منطقة تشبع شرائي قوي، احذر من التصحيح!")
            alerts_log['alerts'].append({'key': alert_key, 'type': 'bb_upper', 'time': saudi_time['full']})
            
    if ind['price'] < ind['bb_lower']:
        alert_key = f"{today}_bb_lower"
        if not any(a['key'] == alert_key for a in alerts_log['alerts']):
            new_alerts.append(f"📉 <b>اختراق Bollinger Band السفلي!</b>\n🕐 {saudi_time['time']} السعودية\n\n💰 السعر: ${ind['price']} كسر ${ind['bb_lower']}\n🔍 السوق في منطقة تشبع بيعي، قد يكون فرصة ارتداد!")
            alerts_log['alerts'].append({'key': alert_key, 'type': 'bb_lower', 'time': saudi_time['full']})
    
    if whales and len(whales) > 0:
        alert_key = f"{today}_whale_activity"
        if not any(a['key'] == alert_key for a in alerts_log['alerts']):
            whale_msg = f"🦈 <b>نشاط حيتان غير عادي!</b>\n🕐 {saudi_time['time']} السعودية\n\n"
            for w in whales[:2]:
                emoji = "🟢" if w['type'] == 'CALL' else "🔴"
                whale_msg += f"{emoji} {w['type']} Strike ${w['strike']}\n   الحجم: {w['volume']} (×{w['ratio']} من المعدل)\n\n"
            new_alerts.append(whale_msg)
            alerts_log['alerts'].append({'key': alert_key, 'type': 'whale', 'time': saudi_time['full']})
    
    if len(alerts_log['alerts']) > 50: alerts_log['alerts'] = alerts_log['alerts'][-50:]
    save_json(ALERTS_LOG, alerts_log)
    return new_alerts

def generate_daily_report():
    brain = get_brain()
    history = load_json(SIGNALS_FILE, {'signals': []})
    saudi_time = get_saudi_time()
    today = saudi_time['date']
    today_signals = [s for s in history['signals'] if s['timestamp'].startswith(today)]
    correct = sum(1 for s in today_signals if s['status'] == 'correct')
    wrong = sum(1 for s in today_signals if s['status'] == 'wrong')
    pending = sum(1 for s in today_signals if s['status'] == 'pending')
    pnl_data = calculate_live_pnl()
    
    msg = f"📊 <b>تقرير نهاية اليوم الشامل</b>\n"
    msg += f"📅 {saudi_time['weekday']} {saudi_time['date']} | 🕐 {saudi_time['time']} السعودية\n\n"
    msg += f"<b>أداء البوت اليوم:</b>\n"
    msg += f"✅ صحيح: {correct} | ❌ خاطئ: {wrong} | ⏳ معلق: {pending}\n"
    if correct + wrong > 0: msg += f"🎯 الدقة: {(correct / (correct + wrong)) * 100:.1f}%\n"
    
    if pnl_data:
        emoji = "🟢" if pnl_data['total_pnl'] >= 0 else "🔴"
        msg += f"\n💼 <b>ملخص المحفظة الحي (P&L):</b>\n"
        msg += f"{emoji} <b>إجمالي الربح/الخسارة:</b> ${pnl_data['total_pnl']}\n"
        for p in pnl_data['positions'][:3]:
            p_emoji = "🟢" if p['pnl'] >= 0 else "🔴"
            msg += f"• {p_emoji} {p['type']} {p['strike']}: ${p['pnl']} ({p['pnl_pct']}%)\n"
            
    if brain['learning_history']:
        msg += f"\n🧠 <b>آخر درس تعلمه البوت:</b>\n"
        for lesson in brain['learning_history'][-1].get('lessons', []): msg += f"• {lesson}\n"
    return msg

def generate_weekly_report():
    brain = get_brain()
    saudi_time = get_saudi_time()
    total = brain['total_predictions']
    correct = brain['correct_predictions']
    wrong = brain['wrong_predictions']
    accuracy = (correct / total * 100) if total > 0 else 0
    win_rate = (correct / (correct + wrong) * 100) if (correct + wrong) > 0 else 0
    msg = f"📈 <b>التقرير الأسبوعي الشامل</b>\n"
    msg += f"📅 {saudi_time['weekday']} {saudi_time['date']} | 🕐 {saudi_time['time']} السعودية\n\n"
    msg += f"<b>🎯 الدقة العامة:</b>\n"
    msg += f"• إجمالي التوقعات: {total}\n• صحيح: {correct} ✅\n• خاطئ: {wrong} ❌\n"
    msg += f"• <b>نسبة الدقة:</b> {accuracy:.1f}%\n• <b>Win Rate:</b> {win_rate:.1f}%\n\n"
    if brain['accuracy_stats']['by_hour']:
        msg += f"<b>⏰ الأداء حسب الساعة (السعودية):</b>\n"
        for hour, stats in sorted(brain['accuracy_stats']['by_hour'].items())[:5]:
            h_total = stats.get('total', 0)
            h_correct = stats.get('correct', 0)
            h_accuracy = (h_correct / h_total * 100) if h_total > 0 else 0
            emoji = "🟢" if h_accuracy >= 60 else "🟡" if h_accuracy >= 40 else "🔴"
            msg += f"{emoji} {hour}: {h_accuracy:.1f}% ({h_correct}/{h_total})\n"
        msg += "\n"
    success_rate = brain['option_success_rate']
    if success_rate['total_tracked'] > 0:
        call_rate = (success_rate['calls'] / max(1, success_rate['total_tracked'] // 2)) * 100
        put_rate = (success_rate['puts'] / max(1, success_rate['total_tracked'] // 2)) * 100
        msg += f"<b>💎 أداء العقود المقترحة:</b>\n"
        msg += f"• CALLs: {call_rate:.1f}% نجاح\n• PUTs: {put_rate:.1f}% نجاح\n• إجمالي: {success_rate['total_tracked']} عقد\n\n"
    if brain['learning_history']:
        msg += f"<b>🧠 آخر الدروس:</b>\n"
        for lesson in brain['learning_history'][-1].get('lessons', [])[:3]: msg += f"• {lesson}\n"
    return msg

def send_telegram(msg, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        if len(msg) > 4000:
            for i in range(0, len(msg), 4000):
                requests.post(url, json={"chat_id": CHAT_ID, "text": msg[i:i+4000], "parse_mode": parse_mode}, timeout=10)
                time.sleep(0.5)
            return True
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": parse_mode}, timeout=10)
        return True
    except: return False

def get_bot_id():
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=5).json()
        return r['result']['id'] if r.get('ok') else None
    except: return None

def get_last_update_id():
    data = load_json(PROCESSED_FILE, {'last_update_id': 0})
    return data.get('last_update_id', 0)

def save_last_update_id(update_id):
    save_json(PROCESSED_FILE, {'last_update_id': update_id})

def get_option_data(strike):
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options: return "❌ لا توجد بيانات."
        exp = ticker.options[0]
        chain = ticker.option_chain(exp)
        c = chain.calls.iloc[(chain.calls['strike'] - strike).abs().argsort()[:1].item()]
        p = chain.puts.iloc[(chain.puts['strike'] - strike).abs().argsort()[:1].item()]
        call_info = get_option_full_info(strike, 'CALL', exp)
        put_info = get_option_full_info(strike, 'PUT', exp)
        msg = f"🔥 <b>بيانات العقد: Strike ${strike}</b>\n"
        msg += f"📅 تاريخ الانتهاء: {exp}\n\n"
        msg += f"🟢 <b>CALL:</b>\n"
        msg += f"   💵 السعر: ${c['lastPrice']:.2f}\n"
        msg += f"   📊 IV: {c['impliedVolatility']*100:.1f}% | Delta: {c['delta']:.2f}\n"
        msg += f"   📦 Volume: {int(c['volume'])} | Open Interest: {int(c.get('openInterest', 0))}\n"
        if call_info:
            msg += f"   🆔 <b>رقم العقد:</b> <code>{call_info['symbol']}</code>\n"
            msg += f"   ⏳ <b>المدة:</b> {call_info['dte_text']} ({call_info['dte']} يوم)\n"
            msg += f"   💹 Bid: ${call_info['bid']} | Ask: ${call_info['ask']} | Spread: ${call_info['spread']}\n"
            msg += f"   📈 In The Money: {'نعم ✅' if call_info['in_the_money'] else 'لا ❌'}\n"
            msg += f"   🕐 آخر تداول: {call_info['last_trade']}\n"
        msg += f"\n🔴 <b>PUT:</b>\n"
        msg += f"   💵 السعر: ${p['lastPrice']:.2f}\n"
        msg += f"   📊 IV: {p['impliedVolatility']*100:.1f}% | Delta: {p['delta']:.2f}\n"
        msg += f"   📦 Volume: {int(p['volume'])} | Open Interest: {int(p.get('openInterest', 0))}\n"
        if put_info:
            msg += f"   🆔 <b>رقم العقد:</b> <code>{put_info['symbol']}</code>\n"
            msg += f"   ⏳ <b>المدة:</b> {put_info['dte_text']} ({put_info['dte']} يوم)\n"
            msg += f"   💹 Bid: ${put_info['bid']} | Ask: ${put_info['ask']} | Spread: ${put_info['spread']}\n"
            msg += f"   📈 In The Money: {'نعم ✅' if put_info['in_the_money'] else 'لا ❌'}\n"
            msg += f"   🕐 آخر تداول: {put_info['last_trade']}\n"
        return msg
    except Exception as e: 
        return f"❌ خطأ: {str(e)}"

def handle_commands():
    bot_id = get_bot_id()
    last_update_id = get_last_update_id()
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id + 1}&limit=50&timeout=30", timeout=35).json()
        if not r.get('ok'): return
        updates = r.get('result', [])
        if not updates: return
        settings = get_settings()
        ind = calculate_indicators()
        brain = get_brain()
        max_update_id = last_update_id
        for update in updates:
            update_id = update['update_id']
            if update_id > max_update_id: max_update_id = update_id
            message = update.get('message', {})
            if not message: continue
            sender = message.get('from', {})
            if sender.get('is_bot', False) or (bot_id and sender.get('id') == bot_id): continue
            text = message.get('text', '').strip()
            chat_id = str(message.get('chat', {}).get('id', ''))
            if chat_id != CHAT_ID: continue
            
            smart_response = handle_smart_chat(text.lower(), ind, brain)
            if smart_response:
                send_telegram(smart_response)
                continue
            
            if text == '/help':
                send_telegram("🤖 <b>أوامر بوت SPX:</b>\n\n💬 'كيف السوق؟' 'هل اشتري؟'\n\n🧠 /predict /brain /spx /results /stats\n🔥 /option /buy_call /buy_put /portfolio /close\n💎 /cheap\n📊 /levels /sentiment /whales /strategy /daily /events /whatif")
            elif text == '/predict':
                pred = generate_prediction()
                if pred: send_telegram(pred)
            elif text == '/brain':
                acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
                msg = f"🧠 <b>عقل البوت:</b>\n🎯 الدقة: {acc:.1f}%\n📏 RSI شراء: < {brain['rsi_buy_threshold']}\n📏 RSI بيع: > {brain['rsi_sell_threshold']}\n🛡️ VIX: > {brain['vix_fear_level']}\n\n<b>أخطاء:</b>\n"
                for p, c in brain['mistake_patterns'].items(): msg += f"• {p}: {c}\n"
                msg += f"\n📚 آخر درس: {brain['last_adjustment_reason']}"
                send_telegram(msg)
            elif text == '/cheap':
                send_telegram("🔍 جاري البحث...")
                report = generate_morning_report()
                if report: send_telegram(report)
            elif text == '/results':
                results = check_prediction_results()
                if results: send_telegram(f"📊 تم فحص {len(results)} توقع")
                else: send_telegram("لا توجد توقعات جديدة")
            elif text == '/stats':
                acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
                send_telegram(f"📊 <b>إحصائيات:</b>\n🎯 الدقة: {acc:.1f}%\n✅ {brain['correct_predictions']} | ❌ {brain['wrong_predictions']}\n📈 إجمالي: {brain['total_predictions']}")
            elif text == '/spx':
                if ind: send_telegram(f"📊 <b>SPX/SPY</b>\n💰 ${ind['price']}\n📈 RSI: {ind['rsi']}\n📉 MACD: {ind['macd']}\n😱 VIX: {ind['vix']}\n📊 SMA50: ${ind['sma_50']}")
            elif text == '/levels':
                levels = calculate_support_resistance()
                if levels: send_telegram(f"🗺️ <b>الدعم والمقاومة</b>\n💰 ${levels['current_price']}\n\n🔴 مقاومة:\n" + "\n".join([f"• ${r}" for r in levels['resistance']]) + f"\n\n🟢 دعم:\n" + "\n".join([f"• ${s}" for s in levels['support']]))
            elif text == '/sentiment':
                sent, arts = analyze_news_sentiment()
                emoji = "🟢" if sent == 'positive' else "🔴" if sent == 'negative' else "🟡"
                send_telegram(f"📰 <b>{sent.upper()}</b> {emoji}\n\n" + "\n".join([f"• {a['title'][:60]}..." for a in arts]))
            elif text == '/whales':
                whales = detect_unusual_activity()
                if whales: send_telegram("🦈 <b>نشاط الحيتان</b>\n\n" + "\n\n".join([f"{'🟢' if w['type'] == 'CALL' else '🔴'} <b>{w['type']} {w['strike']}</b>\nVol: {w['volume']} (×{w['ratio']})" for w in whales]))
                else: send_telegram("🦈 لا نشاط غير طبيعي")
            elif text == '/strategy':
                if ind:
                    strat = suggest_strategy(ind, brain)
                    send_telegram(f"🎯 <b>{strat['name']}</b>\n\n{strat['desc']}\n\n⚡ {strat['action']}")
            elif text == '/daily':
                send_telegram("📊 جاري التقرير...")
                report = generate_daily_report()
                if report: send_telegram(report)
            elif text == '/events':
                send_telegram("📅 <b>الأحداث الاقتصادية:</b>\n• FOMC: قرارات الفائدة\n• CPI: بيانات التضخم\n• NFP: الوظائف غير الزراعية\n\n⚠️ البوت يحذر تلقائياً إذا كان VIX مرتفعاً وقت الأحداث المهمة.")
            elif text == '/portfolio': 
                pnl_data = calculate_live_pnl()
                data = load_json(PORTFOLIO_FILE, {'positions': []})
                if not data['positions']: send_telegram("📂 <b>المحفظة فارغة</b>")
                else:
                    msg = "💼 <b>المحفظة</b>\n━━━━━━━━━━━━━━━\n"
                    for i, pos in enumerate(data['positions'], 1):
                        cost = pos['entry'] * 100 * pos['qty']
                        emoji = "🟢" if pos['type'] == 'CALL' else "🔴"
                        pnl_info = ""
                        if pnl_data and i <= len(pnl_data['positions']):
                            p = pnl_data['positions'][i-1]
                            pnl_info = f" | {'🟢' if p['pnl'] >= 0 else '🔴'} ${p['pnl']} ({p['pnl_pct']}%)"
                        msg += f"{i}. {emoji} <b>{pos['type']} {pos['strike']}</b> | ${pos['entry']} × {pos['qty']}{pnl_info}\n"
                    if pnl_data: msg += f"\n━━━━━━━━━━━━━━━\n{'🟢' if pnl_data['total_pnl'] >= 0 else '🔴'} <b>الإجمالي:</b> ${pnl_data['total_pnl']}"
                    send_telegram(msg)
            elif text.startswith('/option '):
                try: send_telegram(get_option_data(float(text.split()[1])))
                except: send_telegram("❌ /option 500")
            elif text.startswith('/buy_call '):
                try:
                    parts = text.split()
                    settings = get_settings()
                    ind = calculate_indicators()
                    warning = ""
                    if ind and ind['vix'] > 30: warning = "\n\n🚨 <b>تحذير:</b> VIX مرتفع جداً (>30). مخاطرة عالية!"
                    elif ind and ind['rsi'] > 75: warning = "\n\n⚠️ <b>تحذير:</b> RSI > 75, تشبع شرائي!"
                    data = load_json(PORTFOLIO_FILE, {'positions': []})
                    total_cost = float(parts[2]) * 100 * int(parts[3])
                    if total_cost > (settings['capital'] * (settings['risk_percent'] / 100)):
                        send_telegram(f"⚠️ التكلفة (${total_cost:.2f}) تتجاوز المخاطرة!{warning}")
                    else:
                        data['positions'].append({'type': 'CALL', 'strike': float(parts[1]), 'entry': float(parts[2]), 'qty': int(parts[3]), 'date': datetime.now(timezone.utc).strftime('%Y-%m-%d')})
                        save_json(PORTFOLIO_FILE, data)
                        send_telegram(f"✅ تم تسجيل CALL Strike {parts[1]} @ ${parts[2]} × {parts[3]}{warning}")
                except: send_telegram("❌ /buy_call 500 5.0 1")
            elif text.startswith('/buy_put '):
                try:
                    parts = text.split()
                    settings = get_settings()
                    ind = calculate_indicators()
                    warning = ""
                    if ind and ind['vix'] > 30: warning = "\n\n🚨 <b>تحذير:</b> VIX مرتفع جداً (>30). مخاطرة عالية!"
                    data = load_json(PORTFOLIO_FILE, {'positions': []})
                    total_cost = float(parts[2]) * 100 * int(parts[3])
                    if total_cost > (settings['capital'] * (settings['risk_percent'] / 100)):
                        send_telegram(f"⚠️ التكلفة (${total_cost:.2f}) تتجاوز المخاطرة!{warning}")
                    else:
                        data['positions'].append({'type': 'PUT', 'strike': float(parts[1]), 'entry': float(parts[2]), 'qty': int(parts[3]), 'date': datetime.now(timezone.utc).strftime('%Y-%m-%d')})
                        save_json(PORTFOLIO_FILE, data)
                        send_telegram(f"✅ تم تسجيل PUT Strike {parts[1]} @ ${parts[2]} × {parts[3]}{warning}")
                except: send_telegram("❌ /buy_put 500 5.0 1")
            elif text.startswith('/close '):
                try:
                    data = load_json(PORTFOLIO_FILE, {'positions': []})
                    idx = int(text.split()[1])
                    if 0 < idx <= len(data['positions']):
                        removed = data['positions'].pop(idx - 1)
                        save_json(PORTFOLIO_FILE, data)
                        send_telegram(f"✅ تم حذف: {removed['type']} {removed['strike']}")
                    else: send_telegram("❌ رقم غير صحيح")
                except: send_telegram("❌ /close 1")
            elif text.startswith('/whatif '):
                try: send_telegram(calculate_what_if(float(text.split()[1])))
                except: send_telegram("❌ /whatif 510")
            else: send_telegram("🤔 اسألني: 'كيف السوق؟' أو اكتب /help")
        save_last_update_id(max_update_id)
    except Exception as e: print(f"خطأ: {e}")

def run_autonomous_scan():
    print("🤖 بدء الفحص التلقائي...")
    saudi_time = get_saudi_time()
    print(f"🕐 الوقت الحالي (السعودية): {saudi_time['time_with_seconds']}")
    
    track_option_performance()
    
    learned, lessons = deep_learn_from_mistakes()
    if learned:
        send_telegram(f"🧠 <b>تعلم جديد!</b>\n🕐 {saudi_time['time']} السعودية\n\n" + "\n".join([f"• {l}" for l in lessons]))
    
    settings = get_settings()
    today_str = saudi_time['date']
    current_hour = saudi_time['hour']
    
    if current_hour == 14 and settings.get('last_morning_report') != today_str:
        morning_report = generate_morning_report()
        if morning_report: send_telegram(morning_report)
        settings['last_morning_report'] = today_str
        save_json(SETTINGS_FILE, settings)
    
    brain = get_brain()
    last_pred = brain.get('last_prediction_sent')
    minutes_since = 999 if not last_pred else (datetime.now(timezone.utc) - datetime.fromisoformat(last_pred)).total_seconds() / 60
    if minutes_since >= 5:
        pred = generate_prediction()
        if pred:
            options_report = get_periodic_options_report()
            if options_report: pred += f"\n\n{options_report}"
            send_telegram(pred)
    
    if current_hour in [3, 9, 15, 21]:
        last_check = settings.get('last_results_check')
        check_key = f"{today_str}_{current_hour}"
        if last_check != check_key:
            results = check_prediction_results()
            if results: send_telegram(f"📊 <b>فحص النتائج التلقائي</b>\n🕐 {saudi_time['time']} السعودية\nتم فحص {len(results)} توقع")
            settings['last_results_check'] = check_key
            save_json(SETTINGS_FILE, settings)
    
    if current_hour == 22 and settings.get('last_daily_report') != today_str:
        daily_report = generate_daily_report()
        if daily_report: send_telegram(daily_report)
        settings['last_daily_report'] = today_str
        save_json(SETTINGS_FILE, settings)
    
    if saudi_time['weekday'] == 'الأحد' and current_hour == 23:
        last_weekly = settings.get('last_weekly_stats')
        weekly_key = f"{today_str}_weekly"
        if last_weekly != weekly_key:
            weekly_report = generate_weekly_report()
            if weekly_report: send_telegram(weekly_report)
            settings['last_weekly_stats'] = weekly_key
            save_json(SETTINGS_FILE, settings)
    
    if current_hour == 15 and saudi_time['minute'] >= 25 and settings.get('last_pre_close') != today_str:
        ind = calculate_indicators()
        if ind:
            brain = get_brain()
            acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
            msg = f"⏰ <b>ملخص ما قبل الإغلاق التلقائي</b>\n🕐 {saudi_time['time']} السعودية\n\n"
            msg += f"💰 SPY: ${ind['price']}\n📈 RSI: {ind['rsi']} | 😱 VIX: {ind['vix']}\n"
            msg += f"🎯 دقة اليوم: {acc:.1f}%\n\n"
            msg += f"⚠️ السوق سيغلق خلال 30 دقيقة.\nراجع صفقاتك واتخذ قراراتك!"
            send_telegram(msg)
        settings['last_pre_close'] = today_str
        save_json(SETTINGS_FILE, settings)
    
    ind = calculate_indicators()
    if ind:
        levels = calculate_support_resistance()
        whales = detect_unusual_activity()
        auto_alerts = check_auto_alerts(ind, levels, whales)
        if auto_alerts:
            for alert in auto_alerts: send_telegram(alert)

# ==========================================
# ⬇️ تم تعديل هذا الجزء ليعمل مع GitHub Actions ⬇️
# ==========================================
if __name__ == '__main__':
    print("🚀 بدء تشغيل بوت SPX (نسخة GitHub Actions)...")
    saudi_time = get_saudi_time()
    print(f"🕐 الوقت الحالي (السعودية): {saudi_time['time_with_seconds']}")
    
    # إرسال رسالة تشغيل للتأكد من أن البوت يعمل
    send_telegram(f"🟢 <b>تم تشغيل الفحص الدوري</b>\n🕐 {saudi_time['time_with_seconds']} (السعودية)")

    # 1. معالجة الأوامر اليدوية من تيليجرام
    handle_commands()

    # 2. تشغيل المهام التلقائية (تقارير، تنبيهات، تعلم)
    run_autonomous_scan()

    print("✅ انتهت الدورة الحالية. في انتظار التشغيل التالي من GitHub...")
