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
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8672604524:AAHylXIgm78_Mi6LEf1AueOUl4cUxpiaKFA')
CHAT_ID = os.environ.get('CHAT_ID', '208377256')

# ملفات البيانات
PORTFOLIO_FILE = 'options_portfolio.json'
SETTINGS_FILE = 'spx_settings.json'
PROCESSED_FILE = 'processed_spx.json'
BRAIN_FILE = 'bot_brain.json'
SIGNALS_FILE = 'signals_history.json'
NEWS_CACHE = 'news_cache.json'
CHEAP_OPTIONS_CACHE = 'cheap_options_cache.json'
PROMISING_OPTIONS_FILE = 'promising_options.json'

# Cache
_indicators_cache = {'data': None, 'timestamp': 0}
_CACHE_DURATION = 300

# ==========================================
# 🕐 التوقيت السعودي الدقيق
# ==========================================
def get_saudi_time():
    utc_now = datetime.now(timezone.utc)
    saudi_tz = timezone(timedelta(hours=3))
    saudi_now = utc_now.astimezone(saudi_tz)
    return {
        'full': saudi_now.strftime('%Y-%m-%d %H:%M:%S'),
        'date': saudi_now.strftime('%Y-%m-%d'),
        'time': saudi_now.strftime('%H:%M'),
        'time_with_seconds': saudi_now.strftime('%H:%M:%S'),
        'hour': saudi_now.hour,
        'minute': saudi_now.minute,
        'weekday': saudi_now.weekday(),
        'weekday_name': ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد'][saudi_now.weekday()]
    }

# ==========================================
# دوال مساعدة
# ==========================================
def load_json(fn, default=None):
    if default is None: default = {}
    try:
        with open(fn, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

def save_json(fn, data):
    with open(fn, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)

def get_settings():
    defaults = {'capital': 10000, 'risk_percent': 2.0, 'last_daily_report': None, 'last_cheap_search': None, 'last_results_check': None, 'last_weekly_stats': None, 'last_morning_report': None}
    settings = load_json(SETTINGS_FILE, defaults)
    for key in defaults:
        if key not in settings: settings[key] = defaults[key]
    return settings

# ==========================================
# 🧠 نظام العقل والتعلم
# ==========================================
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

# ==========================================
# 💎 البحث عن عقود واعدة
# ==========================================
def find_promising_cheap_options():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options: return None, "❌ لا توجد بيانات."
        
        ind = calculate_indicators()
        if not ind: return None, "❌ لا يمكن جلب المؤشرات."
        
        brain = get_brain()
        expirations = ticker.options[:3] if len(ticker.options) >= 3 else ticker.options
        
        promising_calls = []
        promising_puts = []
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
                            promising_calls.append({
                                'type': 'CALL', 'strike': row['strike'], 'price': row['lastPrice'],
                                'exp': exp, 'volume': int(row['volume']), 'iv': row['impliedVolatility'] * 100,
                                'delta': row['delta'], 'distance_pct': round(distance, 2),
                                'confidence': confidence, 'reason': 'RSI منخفض + MACD إيجابي' if ind['rsi'] < 35 and ind['macd'] > ind['macd_signal'] else 'ظروف سوقية مناسبة'
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
                            promising_puts.append({
                                'type': 'PUT', 'strike': row['strike'], 'price': row['lastPrice'],
                                'exp': exp, 'volume': int(row['volume']), 'iv': row['impliedVolatility'] * 100,
                                'delta': row['delta'], 'distance_pct': round(distance, 2),
                                'confidence': confidence, 'reason': 'RSI مرتفع + MACD سلبي' if ind['rsi'] > 65 and ind['macd'] < ind['macd_signal'] else 'ظروف سوقية مناسبة'
                            })
        
        promising_calls.sort(key=lambda x: x['confidence'], reverse=True)
        promising_puts.sort(key=lambda x: x['confidence'], reverse=True)
        
        options_data = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'current_price': current_price,
            'calls': promising_calls[:5],
            'puts': promising_puts[:5],
            'status': 'pending'
        }
        
        history = load_json(PROMISING_OPTIONS_FILE, {'options': []})
        history['options'].append(options_data)
        if len(history['options']) > 100:
            history['options'] = history['options'][-100:]
        save_json(PROMISING_OPTIONS_FILE, history)
        
        return options_data, None
    except Exception as e:
        return None, f"❌ خطأ: {str(e)}"

def track_option_performance():
    history = load_json(PROMISING_OPTIONS_FILE, {'options': []})
    brain = get_brain()
    updated = False
    
    for batch in history['options']:
        if batch.get('status') == 'tracked':
            continue
        
        batch_time = datetime.fromisoformat(batch['timestamp'])
        hours_since = (datetime.now(timezone.utc) - batch_time).total_seconds() / 3600
        
        if hours_since < 24:
            continue
        
        try:
            current_data = yf.Ticker('SPY').history(period='1d')
            if len(current_data) < 1: continue
            current_price = float(current_data['Close'].iloc[-1])
            predicted_price = batch['current_price']
            change_pct = ((current_price - predicted_price) / predicted_price) * 100
            
            calls_correct = 0
            puts_correct = 0
            calls_total = len(batch.get('calls', []))
            puts_total = len(batch.get('puts', []))
            
            for call in batch.get('calls', []):
                if change_pct > 0.5:
                    calls_correct += 1
            
            for put in batch.get('puts', []):
                if change_pct < -0.5:
                    puts_correct += 1
            
            batch['status'] = 'tracked'
            batch['actual_change'] = round(change_pct, 2)
            batch['calls_correct'] = calls_correct
            batch['puts_correct'] = puts_correct
            
            brain['option_success_rate']['total_tracked'] += calls_total + puts_total
            brain['option_success_rate']['calls'] += calls_correct
            brain['option_success_rate']['puts'] += puts_correct
            
            updated = True
        except:
            continue
    
    if updated:
        save_json(PROMISING_OPTIONS_FILE, history)
        save_brain(brain)
    
    return updated

# ==========================================
# 🌅 تقرير الصباح الشامل
# ==========================================
def generate_morning_report():
    ind = calculate_indicators()
    if not ind: return None
    
    brain = get_brain()
    options_data, error = find_promising_cheap_options()
    if error: return error
    if not options_data: return "❌ لا توجد عقود واعدة حالياً."
    
    saudi_time = get_saudi_time()
    
    msg = f"🌅 <b>تقرير الصباح الشامل</b>\n"
    msg += f" {saudi_time['weekday']} {saudi_time['date']}\n"
    msg += f"🕐 {saudi_time['time_with_seconds']} (توقيت السعودية)\n\n"
    
    msg += f"<b>📊 حالة السوق:</b>\n"
    msg += f"💰 SPY: ${ind['price']}\n"
    msg += f"📈 RSI: {ind['rsi']} | MACD: {'إيجابي ✅' if ind['macd'] > ind['macd_signal'] else 'سلبي '}\n"
    msg += f"😱 VIX: {ind['vix']} ({'منخفض ' if ind['vix'] < 20 else 'مرتفع 🟠'})\n\n"
    
    if options_data['calls']:
        msg += f"🟢 <b>أفضل 5 عقود CALL واعدة:</b>\n\n"
        for i, c in enumerate(options_data['calls'][:5], 1):
            potential = ((c['strike'] - ind['price']) / c['price']) * 100 if c['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${c['strike']}</b> | 💵 ${c['price']} |  {c['exp']}\n"
            msg += f"   🎯 الثقة: {c['confidence']}% | البعد: {c['distance_pct']}%\n"
            msg += f"   📊 IV: {c['iv']:.1f}% | Delta: {c['delta']:.2f} | Vol: {c['volume']}\n"
            msg += f"   💰 العائد المحتمل: +{potential:.0f}%\n"
            msg += f"   🧠 السبب: {c['reason']}\n\n"
    
    if options_data['puts']:
        msg += f"🔴 <b>أفضل 5 عقود PUT واعدة:</b>\n\n"
        for i, p in enumerate(options_data['puts'][:5], 1):
            potential = ((ind['price'] - p['strike']) / p['price']) * 100 if p['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${p['strike']}</b> | 💵 ${p['price']} | 📅 {p['exp']}\n"
            msg += f"   🎯 الثقة: {p['confidence']}% | البعد: {p['distance_pct']}%\n"
            msg += f"    IV: {p['iv']:.1f}% | Delta: {p['delta']:.2f} | Vol: {p['volume']}\n"
            msg += f"   💰 العائد المحتمل: +{potential:.0f}%\n"
            msg += f"   🧠 السبب: {p['reason']}\n\n"
    
    success_rate = brain['option_success_rate']
    if success_rate['total_tracked'] > 0:
        call_rate = (success_rate['calls'] / max(1, success_rate['total_tracked'] // 2)) * 100
        put_rate = (success_rate['puts'] / max(1, success_rate['total_tracked'] // 2)) * 100
        msg += f"<b> سجل النجاح السابق:</b>\n"
        msg += f"• CALLs: {call_rate:.1f}% نجاح\n"
        msg += f"• PUTs: {put_rate:.1f}% نجاح\n"
        msg += f"• إجمالي المتابع: {success_rate['total_tracked']} عقد\n\n"
    
    msg += f"⚠️ <b>تنبيه:</b> هذه اقتراحات بناءً على التحليل الفني. استخدم إدارة رأس مال صارمة."
    
    return msg

# ==========================================
# 💎 التقارير الدورية
# ==========================================
def get_periodic_options_report():
    ind = calculate_indicators()
    if not ind: return None
    
    brain = get_brain()
    options_data, error = find_promising_cheap_options()
    if error or not options_data: return None
    
    saudi_time = get_saudi_time()
    
    msg = f"💎 <b>عقود واعدة الآن</b>\n"
    msg += f" {saudi_time['weekday']} {saudi_time['date']}\n"
    msg += f"🕐 {saudi_time['time']} (السعودية) | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
    msg += f"💰 SPY: ${ind['price']} | RSI: {ind['rsi']} | VIX: {ind['vix']}\n\n"
    
    if options_data['calls']:
        msg += f"🟢 <b>أفضل 3 CALLs واعدة:</b>\n\n"
        for i, c in enumerate(options_data['calls'][:3], 1):
            potential = ((c['strike'] - ind['price']) / c['price']) * 100 if c['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${c['strike']}</b> | 💵 ${c['price']} | 📅 {c['exp']}\n"
            msg += f"   🎯 الثقة: {c['confidence']}% | البعد: {c['distance_pct']}%\n"
            msg += f"   📊 IV: {c['iv']:.1f}% | Delta: {c['delta']:.2f} | Vol: {c['volume']}\n"
            msg += f"   💰 العائد المحتمل: +{potential:.0f}%\n"
            msg += f"    السبب: {c['reason']}\n\n"
    
    if options_data['puts']:
        msg += f"🔴 <b>أفضل 3 PUTs واعدة:</b>\n\n"
        for i, p in enumerate(options_data['puts'][:3], 1):
            potential = ((ind['price'] - p['strike']) / p['price']) * 100 if p['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${p['strike']}</b> | 💵 ${p['price']} | 📅 {p['exp']}\n"
            msg += f"   🎯 الثقة: {p['confidence']}% | البعد: {p['distance_pct']}%\n"
            msg += f"   📊 IV: {p['iv']:.1f}% | Delta: {p['delta']:.2f} | Vol: {p['volume']}\n"
            msg += f"   💰 العائد المحتمل: +{potential:.0f}%\n"
            msg += f"   🧠 السبب: {p['reason']}\n\n"
    
    if brain['option_success_rate']['total_tracked'] > 0:
        total = brain['option_success_rate']['total_tracked']
        correct = brain['option_success_rate']['calls'] + brain['option_success_rate']['puts']
        rate = (correct / total) * 100
        msg += f"📊 <b>معدل نجاح البوت:</b> {rate:.1f}%\n"
    
    msg += f"\n⚠️ <b>تنبيه:</b> استخدم إدارة رأس مال صارمة."
    
    return msg

# ==========================================
# 📊 فحص نتائج التوقعات
# ==========================================
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
            signal['checked_at'] = datetime.now(timezone.utc).isoformat()
            signal['hours_to_check'] = round(hours_since, 1)
            
            if not is_correct:
                reasons = analyze_mistake_reasons(signal, change_pct)
                signal['mistake_reasons'] = reasons
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

def analyze_mistake_reasons(signal, change_pct):
    reasons = []
    ind = signal.get('indicators', {})
    rsi = ind.get('rsi', 50)
    if 'CALL' in signal['decision'] and change_pct < 0:
        if rsi > 40: return ['rsi_too_early']
        elif ind.get('vix', 15) > 25: return ['vix_ignored']
        else: return ['market_unpredictable']
    elif 'PUT' in signal['decision'] and change_pct > 0:
        if rsi < 60: return ['rsi_too_early']
        elif ind.get('vix', 15) < 15: return ['vix_ignored']
        else: return ['market_unpredictable']
    return ['unknown']

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

# ==========================================
# 📊 حساب المؤشرات
# ==========================================
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
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1
        vix_data = yf.Ticker('^VIX').history(period='5d')
        result = {
            'rsi': round(rsi, 2), 'macd': round(macd, 4), 'macd_signal': round(macd_signal, 4),
            'sma_20': round(sma_20, 2), 'sma_50': round(sma_50, 2),
            'volume_ratio': round(vol_ratio, 2),
            'price': round(float(close.iloc[-1]), 2), 'vix': round(float(vix_data['Close'].iloc[-1]), 2)
        }
        _indicators_cache = {'data': result, 'timestamp': now}
        return result
    except: return None

def generate_prediction():
    ind = calculate_indicators()
    if not ind: return None
    brain = get_brain()
    saudi_time = get_saudi_time()
    reasons, score = [], 0
    if ind['rsi'] < brain['rsi_buy_threshold']: score += 2; reasons.append(f" RSI منخفض ({ind['rsi']})")
    elif ind['rsi'] > brain['rsi_sell_threshold']: score -= 2; reasons.append(f"📉 RSI مرتفع ({ind['rsi']})")
    if ind['macd'] > ind['macd_signal']: score += 1; reasons.append("✅ MACD إيجابي")
    else: score -= 1; reasons.append(" MACD سلبي")
    if ind['vix'] > brain['vix_fear_level']: score -= 2; reasons.append(f"⚠️ VIX مرتفع ({ind['vix']})")
    else: score += 1; reasons.append(f"🟢 VIX مستقر ({ind['vix']})")
    
    if score >= 3: decision = "🟢 شراء CALL"
    elif score <= -3: decision = "🔴 شراء PUT"
    elif score >= 1: decision = "🟡 CALL بحذر"
    elif score <= -1: decision = "🟠 PUT بحذر"
    else: decision = " لا تدخل"
    
    signal = {'timestamp': datetime.now(timezone.utc).isoformat(), 'price': ind['price'], 'decision': decision, 'score': score, 'reasons': reasons, 'indicators': ind, 'status': 'pending'}
    history = load_json(SIGNALS_FILE, {'signals': []})
    history['signals'].append(signal)
    save_json(SIGNALS_FILE, history)
    brain['last_prediction_sent'] = datetime.now(timezone.utc).isoformat()
    save_brain(brain)
    
    msg = f"🤖 <b>توقع البوت</b>\n"
    msg += f"📅 {saudi_time['weekday']} {saudi_time['date']}\n"
    msg += f"🕐 {saudi_time['time']} السعودية | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
    msg += f"🎯 <b>القرار:</b> {decision}\n💰 <b>السعر:</b> ${ind['price']}\n\n"
    msg += f"<b>الأسباب:</b>\n" + "\n".join([f"• {r}" for r in reasons]) + "\n\n"
    msg += f"🎯 <b>دقة البوت:</b> {(brain['correct_predictions']/max(1, brain['total_predictions']))*100:.1f}%"
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

def handle_commands():
    bot_id = get_bot_id()
    last_update_id = get_last_update_id()
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id + 1}&limit=50&timeout=30", timeout=35).json()
        if not r.get('ok'): return
        updates = r.get('result', [])
        if not updates: return
        settings = get_settings()
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
            if text == '/help':
                send_telegram("🤖 <b>أوامر بوت SPX:</b>\n\n💬 'كيف السوق؟' 'هل اشتري؟'\n\n🧠 /predict /brain /spx /results /stats\n🔥 /option /buy_call /buy_put /portfolio /close\n💎 /cheap\n📊 /levels /sentiment /whales /strategy /daily /whatif")
            elif text == '/predict':
                pred = generate_prediction()
                if pred: send_telegram(pred)
            elif text == '/cheap':
                send_telegram("🔍 جاري البحث...")
                report = generate_morning_report()
                if report: send_telegram(report)
            elif text == '/results':
                results = check_prediction_results()
                if results: send_telegram(f" تم فحص {len(results)} توقع")
                else: send_telegram("لا توجد توقعات جديدة")
            elif text == '/stats':
                brain = get_brain()
                acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
                send_telegram(f"📊 <b>إحصائيات:</b>\n🎯 الدقة: {acc:.1f}%\n✅ {brain['correct_predictions']} | ❌ {brain['wrong_predictions']}\n📈 إجمالي: {brain['total_predictions']}")
            else:
                send_telegram("🤔 اكتب /help")
        save_last_update_id(max_update_id)
    except Exception as e: print(f"خطأ: {e}")

def run_autonomous_scan():
    """🤖 هذا الجزء يعمل تلقائياً بدون أي أوامر"""
    print(" بدء الفحص التلقائي...")
    saudi_time = get_saudi_time()
    print(f"🕐 الوقت الحالي (السعودية): {saudi_time['time_with_seconds']}")
    
    # 1. ✅ تتبع أداء العقود المقترحة سابقاً (تلقائي)
    print("📊 تتبع أداء العقود...")
    track_option_performance()
    
    # 2. ✅ التعلم من الأخطاء (تلقائي)
    print("🧠 التعلم من الأخطاء...")
    learned, lessons = deep_learn_from_mistakes()
    if learned:
        send_telegram(f"🧠 <b>تعلم جديد!</b>\n🕐 {saudi_time['time']} السعودية\n\n" + "\n".join([f"• {l}" for l in lessons]))
        print(f"✅ تعلم {len(lessons)} درس جديد")
    
    settings = get_settings()
    today_str = saudi_time['date']
    current_hour = saudi_time['hour']
    
    # 3. ✅ تقرير الصباح الشامل الساعة 2 ظهراً السعودية (تلقائي)
    if current_hour == 14 and settings.get('last_morning_report') != today_str:
        print(" إرسال تقرير الصباح الشامل...")
        morning_report = generate_morning_report()
        if morning_report:
            send_telegram(morning_report)
            print("✅ تم إرسال تقرير الصباح")
        settings['last_morning_report'] = today_str
        save_json(SETTINGS_FILE, settings)
    
    # 4. ✅ التوقع كل 5 دقائق مع عقود واعدة (تلقائي)
    brain = get_brain()
    last_pred = brain.get('last_prediction_sent')
    minutes_since = 999 if not last_pred else (datetime.now(timezone.utc) - datetime.fromisoformat(last_pred)).total_seconds() / 60
    
    if minutes_since >= 5:
        print("📊 توليد توقع جديد...")
        pred = generate_prediction()
        if pred:
            options_report = get_periodic_options_report()
            if options_report:
                pred += f"\n\n{options_report}"
            send_telegram(pred)
            print("✅ تم إرسال التوقع")
    
    # 5. ✅ فحص النتائج كل 6 ساعات (تلقائي)
    if current_hour in [3, 9, 15, 21]:  # بتوقيت السعودية
        last_check = settings.get('last_results_check')
        check_key = f"{today_str}_{current_hour}"
        if last_check != check_key:
            print(" فحص نتائج التوقعات...")
            results = check_prediction_results()
            if results:
                send_telegram(f"📊 <b>فحص النتائج</b>\n🕐 {saudi_time['time']} السعودية\nتم فحص {len(results)} توقع")
                print(f"✅ تم فحص {len(results)} توقع")
            settings['last_results_check'] = check_key
            save_json(SETTINGS_FILE, settings)
    
    # 6. ✅ تقرير نهاية اليوم الساعة 10 مساءً السعودية (تلقائي)
    if current_hour == 22 and settings.get('last_daily_report') != today_str:
        print("📊 إرسال تقرير نهاية اليوم...")
        brain = get_brain()
        acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
        send_telegram(f"📊 <b>تقرير نهاية اليوم</b>\n {saudi_time['weekday']} {saudi_time['date']}\n🕐 {saudi_time['time']} السعودية\n\n🎯 الدقة: {acc:.1f}%\n✅ {brain['correct_predictions']} | ❌ {brain['wrong_predictions']}")
        settings['last_daily_report'] = today_str
        save_json(SETTINGS_FILE, settings)
        print("✅ تم إرسال تقرير نهاية اليوم")
    
    # 7. ✅ التقرير الأسبوعي الأحد 11 مساءً السعودية (تلقائي)
    if saudi_time['weekday'] == 'الأحد' and current_hour == 23:
        last_weekly = settings.get('last_weekly_stats')
        weekly_key = f"{today_str}_weekly"
        if last_weekly != weekly_key:
            print("📈 إرسال التقرير الأسبوعي...")
            brain = get_brain()
            acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
            send_telegram(f"📈 <b>التقرير الأسبوعي</b>\n📅 {saudi_time['weekday']} {saudi_time['date']}\n🕐 {saudi_time['time']} السعودية\n\n🎯 الدقة الكلية: {acc:.1f}%\n✅ صحيح: {brain['correct_predictions']}\n❌ خاطئ: {brain['wrong_predictions']}\n إجمالي: {brain['total_predictions']}")
            settings['last_weekly_stats'] = weekly_key
            save_json(SETTINGS_FILE, settings)
            print("✅ تم إرسال التقرير الأسبوعي")

if __name__ == '__main__':
    print("🚀 بدء بوت SPX التلقائي...")
    print(f"🕐 الوقت الحالي (السعودية): {get_saudi_time()['time_with_seconds']}")
    
    # ✅ معالجة الأوامر اليدوية (اختياري)
    print("1️⃣ معالجة الأوامر اليدوية...")
    handle_commands()
    
    # ✅ التشغيل التلقائي (يعمل بدون أوامر)
    print("2️⃣ التشغيل التلقائي...")
    run_autonomous_scan()
    
    print("✅ انتهى - البوت يعمل تلقائياً!")
