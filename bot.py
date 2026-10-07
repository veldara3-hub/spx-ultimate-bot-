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

# ملفات البيانات والذاكرة
PORTFOLIO_FILE = 'options_portfolio.json'
SETTINGS_FILE = 'spx_settings.json'
PROCESSED_FILE = 'processed_spx.json'
BRAIN_FILE = 'bot_brain.json'
SIGNALS_FILE = 'signals_history.json'
NEWS_CACHE = 'news_cache.json'
EVENTS_CACHE = 'events_cache.json'
CHEAP_OPTIONS_CACHE = 'cheap_options_cache.json'
RESULTS_CACHE = 'prediction_results.json'

# Cache للمؤشرات الفنية
_indicators_cache = {'data': None, 'timestamp': 0}
_CACHE_DURATION = 300

def load_json(fn, default=None):
    if default is None: default = {}
    try:
        with open(fn, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

def save_json(fn, data):
    with open(fn, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)

def get_settings():
    defaults = {'capital': 10000, 'risk_percent': 2.0, 'last_daily_report': None, 'last_cheap_search': None, 'last_results_check': None, 'last_weekly_stats': None}
    settings = load_json(SETTINGS_FILE, defaults)
    for key in defaults:
        if key not in settings: settings[key] = defaults[key]
    return settings

# ==========================================
# 🧠 نظام العقل والتعلم المتقدم
# ==========================================
def get_brain():
    defaults = {
        'rsi_buy_threshold': 30, 'rsi_sell_threshold': 70, 'vix_fear_level': 25,
        'total_predictions': 0, 'correct_predictions': 0, 'wrong_predictions': 0,
        'last_adjustment_reason': 'لم يبدأ التعلم بعد', 'last_prediction_sent': None,
        'mistake_patterns': {'rsi_too_early': 0, 'vix_ignored': 0, 'macd_false_signal': 0, 'news_conflict': 0, 'timing_error': 0},
        'learning_history': [], 'best_strategy': 'CALL', 'avoid_patterns': [],
        'accuracy_stats': {'total': 0, 'correct': 0, 'wrong': 0, 'by_hour': {}, 'by_strategy': {}}
    }
    brain = load_json(BRAIN_FILE, defaults)
    for key in defaults:
        if key not in brain: brain[key] = defaults[key]
    return brain

def save_brain(brain): save_json(BRAIN_FILE, brain)

# ==========================================
# 🎯 فحص نتائج التوقعات
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
                hour_key = signal_time.strftime('%H:00')
                if hour_key not in brain['accuracy_stats']['by_hour']:
                    brain['accuracy_stats']['by_hour'][hour_key] = {'total': 0, 'wrong': 0}
                brain['accuracy_stats']['by_hour'][hour_key]['total'] += 1
                brain['accuracy_stats']['by_hour'][hour_key]['wrong'] += 1
                strategy = signal.get('strategy', 'Unknown')
                if strategy not in brain['accuracy_stats']['by_strategy']:
                    brain['accuracy_stats']['by_strategy'][strategy] = {'total': 0, 'wrong': 0}
                brain['accuracy_stats']['by_strategy'][strategy]['total'] += 1
                brain['accuracy_stats']['by_strategy'][strategy]['wrong'] += 1
            else:
                brain['correct_predictions'] += 1
                hour_key = signal_time.strftime('%H:00')
                if hour_key not in brain['accuracy_stats']['by_hour']:
                    brain['accuracy_stats']['by_hour'][hour_key] = {'total': 0, 'correct': 0}
                brain['accuracy_stats']['by_hour'][hour_key]['total'] += 1
                brain['accuracy_stats']['by_hour'][hour_key]['correct'] += 1
                strategy = signal.get('strategy', 'Unknown')
                if strategy not in brain['accuracy_stats']['by_strategy']:
                    brain['accuracy_stats']['by_strategy'][strategy] = {'total': 0, 'correct': 0}
                brain['accuracy_stats']['by_strategy'][strategy]['total'] += 1
                brain['accuracy_stats']['by_strategy'][strategy]['correct'] += 1
            
            brain['total_predictions'] += 1
            results.append(signal)
        except Exception as e:
            print(f"خطأ في فحص التوقع: {e}")
            continue
    
    if results:
        save_json(SIGNALS_FILE, history)
        save_brain(brain)
    return results

def analyze_mistake_reasons(signal, change_pct):
    reasons = []
    ind = signal.get('indicators', {})
    rsi = ind.get('rsi', 50)
    if 'CALL' in signal['decision'] and change_pct < 0:
        if rsi > 40:
            reasons.append(f"📉 RSI كان {rsi} (مرتفع نسبياً للشراء)")
            return ['rsi_too_early']
        elif ind.get('vix', 15) > 25:
            reasons.append(f"⚠️ VIX كان مرتفعاً ({ind['vix']})")
            return ['vix_ignored']
        else:
            reasons.append(f"📊 السوق تحرك عكس التوقع")
            return ['market_unpredictable']
    elif 'PUT' in signal['decision'] and change_pct > 0:
        if rsi < 60:
            reasons.append(f"📈 RSI كان {rsi} (منخفض نسبياً للبيع)")
            return ['rsi_too_early']
        elif ind.get('vix', 15) < 15:
            reasons.append(f"🟢 VIX كان منخفضاً جداً ({ind['vix']})")
            return ['vix_ignored']
        else:
            reasons.append(f"❓ السوق تحرك عكس التوقع")
            return ['market_unpredictable']
    return ['unknown']

def get_accuracy_stats():
    brain = get_brain()
    total = brain['total_predictions']
    correct = brain['correct_predictions']
    wrong = brain['wrong_predictions']
    accuracy = (correct / total * 100) if total > 0 else 0
    win_rate = (correct / (correct + wrong) * 100) if (correct + wrong) > 0 else 0
    
    hourly_stats = []
    for hour, stats in sorted(brain['accuracy_stats']['by_hour'].items()):
        h_total = stats.get('total', 0)
        h_correct = stats.get('correct', 0)
        h_wrong = stats.get('wrong', 0)
        h_accuracy = (h_correct / h_total * 100) if h_total > 0 else 0
        hourly_stats.append({'hour': hour, 'total': h_total, 'correct': h_correct, 'wrong': h_wrong, 'accuracy': round(h_accuracy, 1)})
    
    strategy_stats = []
    for strategy, stats in brain['accuracy_stats']['by_strategy'].items():
        s_total = stats.get('total', 0)
        s_correct = stats.get('correct', 0)
        s_wrong = stats.get('wrong', 0)
        s_accuracy = (s_correct / s_total * 100) if s_total > 0 else 0
        strategy_stats.append({'strategy': strategy, 'total': s_total, 'correct': s_correct, 'wrong': s_wrong, 'accuracy': round(s_accuracy, 1)})
    
    return {'total': total, 'correct': correct, 'wrong': wrong, 'accuracy': round(accuracy, 1), 'win_rate': round(win_rate, 1), 'hourly': hourly_stats, 'strategies': strategy_stats}

def generate_results_report(results):
    if not results:
        return "📊 <b>لا توجد توقعات جديدة للفحص.</b>"
    
    correct_count = sum(1 for r in results if r['status'] == 'correct')
    wrong_count = sum(1 for r in results if r['status'] == 'wrong')
    accuracy = (correct_count / len(results) * 100) if len(results) > 0 else 0
    brain = get_brain()
    total_accuracy = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
    
    msg = f" <b>تقرير نتائج التوقعات</b>\n📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
    msg += f"<b>النتائج الجديدة:</b>\n✅ صحيح: {correct_count}\n خاطئ: {wrong_count}\n🎯 دقة هذه الدفعة: {accuracy:.1f}%\n\n"
    msg += f"<b>الدقة الكلية للبوت:</b> {total_accuracy:.1f}%\n"
    msg += f"📈 إجمالي التوقعات: {brain['total_predictions']}\n✅ صحيح: {brain['correct_predictions']}\n❌ خاطئ: {brain['wrong_predictions']}\n\n"
    
    msg += f"<b>📝 تفاصيل التوقعات:</b>\n\n"
    for r in results[:5]:
        emoji = "✅" if r['status'] == 'correct' else ""
        time_diff = r.get('hours_to_check', 24)
        msg += f"{emoji} <b>{r['decision']}</b> @ ${r['price']}\n"
        msg += f"   📅 التوقع: {r['timestamp'][:16]}\n"
        msg += f"   ⏱️ بعد {time_diff} ساعة\n"
        msg += f"   📊 التغيير الفعلي: {r['actual_change']:+.2f}%\n"
        if r['status'] == 'wrong' and 'mistake_reasons' in r:
            reasons = r['mistake_reasons']
            if 'rsi_too_early' in reasons: msg += f"   ⚠️ السبب: RSI لم يكن في المستوى المثالي\n"
            elif 'vix_ignored' in reasons: msg += f"   ⚠️ السبب: تجاهلنا ارتفاع VIX\n"
            elif 'market_unpredictable' in reasons: msg += f"   ⚠️ السبب: تحرك السوق بشكل غير متوقع\n"
        msg += "\n"
    
    if wrong_count > 0:
        msg += f"<b>🧠 الدروس المستفادة:</b>\n"
        if brain['mistake_patterns']['rsi_too_early'] > 2: msg += f"• RSI مبكر: {brain['mistake_patterns']['rsi_too_early']} مرات\n"
        if brain['mistake_patterns']['vix_ignored'] > 2: msg += f"• VIX مرتفع: {brain['mistake_patterns']['vix_ignored']} مرات\n"
        msg += f"\n📏 RSI الشراء الجديد: < {brain['rsi_buy_threshold']}\n"
        msg += f" RSI البيع الجديد: > {brain['rsi_sell_threshold']}\n"
    
    return msg

def generate_full_stats():
    stats = get_accuracy_stats()
    brain = get_brain()
    
    msg = f"📊 <b>إحصائيات البوت الكاملة</b>\n"
    msg += f"📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
    
    msg += f"<b>🎯 الدقة العامة:</b>\n"
    msg += f"• إجمالي التوقعات: {stats['total']}\n"
    msg += f"• صحيح: {stats['correct']} ✅\n"
    msg += f"• خاطئ: {stats['wrong']} \n"
    msg += f"• <b>نسبة الدقة:</b> {stats['accuracy']}%\n"
    msg += f"• <b>نسبة النجاح (Win Rate):</b> {stats['win_rate']}%\n\n"
    
    if stats['hourly']:
        msg += f"<b>⏰ الأداء حسب التوقيت (UTC):</b>\n"
        for h in stats['hourly'][:5]:
            emoji = "🟢" if h['accuracy'] >= 60 else "🟡" if h['accuracy'] >= 40 else "🔴"
            msg += f"{emoji} {h['hour']}: {h['accuracy']}% ({h['correct']}/{h['total']})\n"
        msg += "\n"
    
    if stats['strategies']:
        msg += f"<b> الأداء حسب الاستراتيجية:</b>\n"
        for s in stats['strategies']:
            emoji = "🟢" if s['accuracy'] >= 60 else "🟡" if s['accuracy'] >= 40 else "🔴"
            msg += f"{emoji} {s['strategy']}: {s['accuracy']}% ({s['correct']}/{s['total']})\n"
        msg += "\n"
    
    if stats['hourly']:
        best_hour = max(stats['hourly'], key=lambda x: x['accuracy'])
        worst_hour = min(stats['hourly'], key=lambda x: x['accuracy'])
        msg += f"<b>💡 التوصيات:</b>\n"
        msg += f"• أفضل وقت: {best_hour['hour']} (دقة {best_hour['accuracy']}%)\n"
        msg += f"• أسوأ وقت: {worst_hour['hour']} (دقة {worst_hour['accuracy']}%)\n"
    
    return msg

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
        brain['mistake_patterns']['rsi_too_early'] += 1
        lessons.append(f"📉 أخطأت {len(wrong_calls)} مرات في CALLs. خفضت عتبة RSI إلى {brain['rsi_buy_threshold']}")
        updated = True
    if len(wrong_puts) > 3:
        brain['rsi_sell_threshold'] = min(85, brain['rsi_sell_threshold'] + 3)
        lessons.append(f"📈 أخطأت {len(wrong_puts)} مرات في PUTs. رفعت عتبة RSI إلى {brain['rsi_sell_threshold']}")
        updated = True
    
    accuracy = sum(1 for s in recent_signals if s['status'] == 'correct') / len(recent_signals)
    if accuracy < 0.4:
        brain['avoid_patterns'].append('high_vix_trading')
        lessons.append("⚠️ الدقة منخفضة. سأتجنب التداول عند VIX مرتفع")
        updated = True
    elif accuracy > 0.7:
        lessons.append("✅ أداء ممتاز! سأزيد الثقة في التوقعات")
        updated = True
    
    if lessons:
        brain['learning_history'].append({'date': datetime.now(timezone.utc).isoformat(), 'accuracy': round(accuracy * 100, 1), 'lessons': lessons})
        if len(brain['learning_history']) > 50: brain['learning_history'] = brain['learning_history'][-50:]
        save_brain(brain)
    return updated, lessons

# ==========================================
# 📊 حساب المؤشرات الفنية (مع Cache)
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
        bb_std = close.rolling(20).std().iloc[-1]
        bb_upper, bb_lower = sma_20 + (bb_std * 2), sma_20 - (bb_std * 2)
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1
        vix_data = yf.Ticker('^VIX').history(period='5d')
        result = {
            'rsi': round(rsi, 2), 'macd': round(macd, 4), 'macd_signal': round(macd_signal, 4),
            'sma_20': round(sma_20, 2), 'sma_50': round(sma_50, 2), 'bb_upper': round(bb_upper, 2),
            'bb_lower': round(bb_lower, 2), 'atr': round(atr, 2), 'volume_ratio': round(vol_ratio, 2),
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

def find_cheap_options():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options: return None, "❌ لا توجد بيانات خيارات متاحة."
        expirations = ticker.options[:2] if len(ticker.options) >= 2 else ticker.options
        cheap_calls, cheap_puts = [], []
        current_price = float(yf.Ticker('SPY').history(period='1d')['Close'].iloc[-1])
        for exp in expirations:
            chain = ticker.option_chain(exp)
            for _, row in chain.calls.iterrows():
                if row['lastPrice'] < 2.0 and row['lastPrice'] > 0.1:
                    distance = ((row['strike'] - current_price) / current_price) * 100
                    if distance < 5:
                        cheap_calls.append({'type': 'CALL', 'strike': row['strike'], 'price': row['lastPrice'], 'exp': exp, 'volume': int(row['volume']), 'iv': row['impliedVolatility'] * 100, 'delta': row['delta'], 'distance_pct': round(distance, 2)})
            for _, row in chain.puts.iterrows():
                if row['lastPrice'] < 2.0 and row['lastPrice'] > 0.1:
                    distance = ((current_price - row['strike']) / current_price) * 100
                    if distance < 5:
                        cheap_puts.append({'type': 'PUT', 'strike': row['strike'], 'price': row['lastPrice'], 'exp': exp, 'volume': int(row['volume']), 'iv': row['impliedVolatility'] * 100, 'delta': row['delta'], 'distance_pct': round(distance, 2)})
        cheap_calls.sort(key=lambda x: x['price'])
        cheap_puts.sort(key=lambda x: x['price'])
        save_json(CHEAP_OPTIONS_CACHE, {'last_search': datetime.now(timezone.utc).isoformat(), 'calls': cheap_calls[:5], 'puts': cheap_puts[:5], 'current_price': current_price})
        return {'calls': cheap_calls[:5], 'puts': cheap_puts[:5], 'current_price': current_price}, None
    except Exception as e:
        return None, f"❌ خطأ: {str(e)}"

def get_cheap_options_message():
    data, error = find_cheap_options()
    if error: return error
    if not data or (not data['calls'] and not data['puts']):
        return " لا توجد عقود رخيصة مناسبة حالياً."
    msg = f"💎 <b>أفضل العقود الرخيصة اليوم</b>\n📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n💰 سعر SPY: ${data['current_price']}\n\n"
    if data['calls']:
        msg += "🟢 <b>أفضل 5 CALLs رخيصة:</b>\n\n"
        for i, c in enumerate(data['calls'], 1):
            potential = ((c['strike'] - data['current_price']) / c['price']) * 100 if c['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${c['strike']}</b> | 💵 ${c['price']} | 📅 {c['exp']}\n   📊 IV: {c['iv']:.1f}% | Delta: {c['delta']:.2f} | Vol: {c['volume']}\n   🎯 البعد: {c['distance_pct']}% | 💰 العائد: +{potential:.0f}%\n\n"
    if data['puts']:
        msg += "🔴 <b>أفضل 5 PUTs رخيصة:</b>\n\n"
        for i, p in enumerate(data['puts'], 1):
            potential = ((data['current_price'] - p['strike']) / p['price']) * 100 if p['price'] > 0 else 0
            msg += f"{i}. <b>Strike ${p['strike']}</b> | 💵 ${p['price']} | 📅 {p['exp']}\n   📊 IV: {p['iv']:.1f}% | Delta: {p['delta']:.2f} | Vol: {p['volume']}\n   🎯 البعد: {p['distance_pct']}% | 💰 العائد: +{potential:.0f}%\n\n"
    msg += "⚠️ <b>تنبيه:</b> العقود الرخيصة تحمل مخاطرة عالية."
    return msg

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

def generate_prediction():
    ind = calculate_indicators()
    if not ind: return None
    brain, levels = get_brain(), calculate_support_resistance()
    sentiment, articles = analyze_news_sentiment()
    strategy = suggest_strategy(ind, brain)
    reasons, score = [], 0
    if ind['rsi'] < brain['rsi_buy_threshold']: score += 2; reasons.append(f" RSI منخفض ({ind['rsi']}), فرصة ارتداد")
    elif ind['rsi'] > brain['rsi_sell_threshold']: score -= 2; reasons.append(f"📉 RSI مرتفع ({ind['rsi']}), احتمال تصحيح")
    if ind['macd'] > ind['macd_signal']: score += 1; reasons.append("✅ MACD إيجابي")
    else: score -= 1; reasons.append("❌ MACD سلبي")
    if ind['vix'] > brain['vix_fear_level']: score -= 2; reasons.append(f"⚠️ VIX مرتفع ({ind['vix']})")
    else: score += 1; reasons.append(f"🟢 VIX مستقر ({ind['vix']})")
    if ind['price'] > ind['sma_20']: score += 1; reasons.append(f"📊 السعر فوق SMA20")
    if sentiment == 'positive': score += 1; reasons.append("📰 أخبار إيجابية")
    elif sentiment == 'negative': score -= 1; reasons.append("📰 أخبار سلبية")
    
    if score >= 3: decision, advice = " شراء CALL", "💡 ادخل بـ 50% من حجمك وضع وقف خسارة -20%"
    elif score <= -3: decision, advice = "🔴 شراء PUT", " ادخل بحذر وضع هدف ربح +30%"
    elif score >= 1: decision, advice = "🟡 CALL بحذر", "️ انتظر تأكيداً إضافياً"
    elif score <= -1: decision, advice = " PUT بحذر", "⚠️ انتظر تأكيداً إضافياً"
    else: decision, advice = "⛔ لا تدخل", "💡 حافظ على رأس المال"
    
    signal = {'timestamp': datetime.now(timezone.utc).isoformat(), 'price': ind['price'], 'decision': decision, 'score': score, 'reasons': reasons, 'indicators': ind, 'strategy': strategy['name'], 'status': 'pending'}
    history = load_json(SIGNALS_FILE, {'signals': []})
    history['signals'].append(signal)
    save_json(SIGNALS_FILE, history)
    brain['last_prediction_sent'] = datetime.now(timezone.utc).isoformat()
    save_brain(brain)
    
    msg = f"🤖 <b>توقع البوت</b>\n📅 {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
    msg += f"🎯 <b>القرار:</b> {decision}\n💰 <b>السعر:</b> ${ind['price']}\n🧠 <b>الاستراتيجية:</b> {strategy['name']}\n\n"
    msg += f"<b>الأسباب:</b>\n" + "\n".join([f"• {r}" for r in reasons]) + f"\n\n{advice}\n"
    if levels: msg += f"🗺️ <b>المستويات:</b>\n• مقاومة: {', '.join([f'${r}' for r in levels['resistance']])}\n• دعم: {', '.join([f'${s}' for s in levels['support']])}\n"
    if articles: msg += f"📰 <b>الأخبار:</b>\n" + "\n".join([f"• {art['title'][:50]}..." for art in articles[:2]]) + "\n"
    msg += f"🎯 <b>دقة البوت:</b> {(brain['correct_predictions']/max(1, brain['total_predictions']))*100:.1f}%"
    return msg

def generate_daily_report():
    brain = get_brain()
    history = load_json(SIGNALS_FILE, {'signals': []})
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    today_signals = [s for s in history['signals'] if s['timestamp'].startswith(today)]
    correct = sum(1 for s in today_signals if s['status'] == 'correct')
    wrong = sum(1 for s in today_signals if s['status'] == 'wrong')
    pnl_data = calculate_live_pnl()
    msg = f"📊 <b>تقرير نهاية اليوم</b>\n📅 {today}\n\n<b>أداء البوت:</b>\n✅ {correct} | ❌ {wrong}\n"
    if correct + wrong > 0: msg += f"🎯 الدقة: {(correct / (correct + wrong)) * 100:.1f}%\n"
    if pnl_data: msg += f"\n{'🟢' if pnl_data['total_pnl'] >= 0 else '🔴'} P&L: ${pnl_data['total_pnl']}\n"
    if brain['learning_history']: msg += f"\n آخر درس: {brain['learning_history'][-1].get('lessons', ['لا دروس'])[0]}"
    return msg

def handle_smart_chat(text, ind, brain):
    text = text.lower().strip()
    if any(w in text for w in ['كيف السوق', 'وضع السوق', 'السوق اليوم']):
        if not ind: return "❌ لا يمكن جلب البيانات."
        trend = "صاعد 🟢" if ind['price'] > ind['sma_20'] else "هابط 🔴"
        return f" <b>ملخص سريع:</b>\nالاتجاه: {trend}\nVIX: {ind['vix']}\nRSI: {ind['rsi']}\n\n{' السوق جيد للفرص' if ind['vix'] < 25 else '⚠️ أنصح بالانتظار'}."
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
        chain = ticker.option_chain(ticker.options[0])
        c = chain.calls.iloc[(chain.calls['strike'] - strike).abs().argsort()[:1].item()]
        p = chain.puts.iloc[(chain.puts['strike'] - strike).abs().argsort()[:1].item()]
        return f"🔥 <b>Strike {strike}</b> | 📅 {ticker.options[0]}\n\n CALL: ${c['lastPrice']:.2f} | IV: {c['impliedVolatility']*100:.1f}% | Delta: {c['delta']:.2f}\n🔴 PUT: ${p['lastPrice']:.2f} | IV: {p['impliedVolatility']*100:.1f}% | Delta: {p['delta']:.2f}"
    except: return "❌ خطأ"

def add_option_position(opt_type, strike, entry_price, qty):
    settings = get_settings()
    ind = calculate_indicators()
    warning = ""
    if ind and ind['vix'] > 30: warning = "\n\n🚨 <b>تحذير:</b> VIX مرتفع جداً (>30). مخاطرة عالية!"
    elif ind and ind['rsi'] > 75 and opt_type == 'CALL': warning = "\n\n⚠️ <b>تحذير:</b> RSI > 75, تشبع شرائي!"
    data = load_json(PORTFOLIO_FILE, {'positions': []})
    total_cost = entry_price * 100 * qty
    if total_cost > (settings['capital'] * (settings['risk_percent'] / 100)):
        return False, f"⚠️ التكلفة (${total_cost:.2f}) تتجاوز المخاطرة!{warning}"
    data['positions'].append({'type': opt_type.upper(), 'strike': strike, 'entry': entry_price, 'qty': qty, 'date': datetime.now(timezone.utc).strftime('%Y-%m-%d')})
    save_json(PORTFOLIO_FILE, data)
    return True, f"✅ تم تسجيل {opt_type.upper()} Strike {strike} @ ${entry_price} × {qty}{warning}"

def get_portfolio():
    data = load_json(PORTFOLIO_FILE, {'positions': []})
    if not data['positions']: return "📂 <b>المحفظة فارغة</b>"
    pnl_data = calculate_live_pnl()
    msg = " <b>المحفظة</b>\n━━━━━━━━━━━━━━━\n"
    for i, pos in enumerate(data['positions'], 1):
        cost = pos['entry'] * 100 * pos['qty']
        emoji = "🟢" if pos['type'] == 'CALL' else "🔴"
        pnl_info = ""
        if pnl_data and i <= len(pnl_data['positions']):
            p = pnl_data['positions'][i-1]
            pnl_info = f" | {'' if p['pnl'] >= 0 else '🔴'} ${p['pnl']} ({p['pnl_pct']}%)"
        msg += f"{i}. {emoji} <b>{pos['type']} {pos['strike']}</b> | ${pos['entry']} × {pos['qty']}{pnl_info}\n"
    if pnl_data: msg += f"\n━━━━━━━━━━━━━━━\n{'🟢' if pnl_data['total_pnl'] >= 0 else '🔴'} <b>الإجمالي:</b> ${pnl_data['total_pnl']}"
    return msg

def remove_position(index):
    data = load_json(PORTFOLIO_FILE, {'positions': []})
    if 0 < index <= len(data['positions']):
        removed = data['positions'].pop(index - 1)
        save_json(PORTFOLIO_FILE, data)
        return True, f"✅ تم حذف: {removed['type']} {removed['strike']}"
    return False, "❌ رقم غير صحيح"

def process_message(text, settings):
    text_lower = text.lower().strip()
    ind = calculate_indicators()
    brain = get_brain()
    
    smart_response = handle_smart_chat(text_lower, ind, brain)
    if smart_response:
        send_telegram(smart_response)
        return

    if text == '/help':
        msg = "🤖 <b>أوامر بوت SPX:</b>\n\n💬 <b>دردشة:</b>\n• 'كيف السوق؟'\n• 'هل اشتري؟'\n\n🧠 <b>تحليل:</b>\n/predict - توقع\n/brain - عقل البوت\n/spx - لوحة التحكم\n/results - آخر النتائج\n/stats - إحصائيات كاملة\n\n🔥 <b>خيارات:</b>\n/option [strike]\n/buy_call [strike] [price] [qty]\n/buy_put [strike] [price] [qty]\n/portfolio\n/close [رقم]\n\n💎 <b>عقود رخيصة:</b>\n/cheap\n\n📊 <b>متقدم:</b>\n/levels\n/sentiment\n/whales\n/strategy\n/events\n/daily\n/whatif [السعر]"
        send_telegram(msg)
    elif text == '/predict':
        send_telegram("🧠 جاري التحليل...")
        pred = generate_prediction()
        if pred: send_telegram(pred)
    elif text == '/brain':
        acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
        msg = f"🧠 <b>عقل البوت:</b>\n🎯 الدقة: {acc:.1f}%\n📏 RSI شراء: < {brain['rsi_buy_threshold']}\n📏 RSI بيع: > {brain['rsi_sell_threshold']}\n🛡️ VIX: > {brain['vix_fear_level']}\n\n<b>أخطاء:</b>\n"
        for p, c in brain['mistake_patterns'].items(): msg += f"• {p}: {c}\n"
        msg += f"\n📚 آخر درس: {brain['last_adjustment_reason']}"
        send_telegram(msg)
    elif text == '/results':
        send_telegram("📊 جاري فحص النتائج...")
        results = check_prediction_results()
        report = generate_results_report(results)
        send_telegram(report)
    elif text == '/stats':
        send_telegram(" جاري حساب الإحصائيات...")
        stats_msg = generate_full_stats()
        send_telegram(stats_msg)
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
    elif text == '/events':
        send_telegram("📅 <b>الأحداث:</b>\nتحقق من ForexFactory لـ FOMC و CPI.\n(البوت يحذر إذا VIX مرتفع)")
    elif text == '/daily':
        send_telegram("📊 جاري التقرير...")
        send_telegram(generate_daily_report())
    elif text == '/cheap':
        send_telegram(" جاري البحث...")
        send_telegram(get_cheap_options_message())
    elif text.startswith('/whatif '):
        try: send_telegram(calculate_what_if(float(text.split()[1])))
        except: send_telegram("❌ /whatif 510")
    elif text.startswith('/option '):
        try: send_telegram(get_option_data(float(text.split()[1])))
        except: send_telegram("❌ /option 500")
    elif text.startswith('/buy_call '):
        try:
            parts = text.split()
            s, msg = add_option_position('CALL', float(parts[1]), float(parts[2]), int(parts[3]))
            send_telegram(msg)
        except: send_telegram("❌ /buy_call 500 5.0 1")
    elif text.startswith('/buy_put '):
        try:
            parts = text.split()
            s, msg = add_option_position('PUT', float(parts[1]), float(parts[2]), int(parts[3]))
            send_telegram(msg)
        except: send_telegram("❌ /buy_put 500 5.0 1")
    elif text == '/portfolio': send_telegram(get_portfolio())
    elif text.startswith('/close '):
        try:
            s, msg = remove_position(int(text.split()[1]))
            send_telegram(msg)
        except: send_telegram("❌ /close 1")
    elif text.startswith('/capital '):
        try:
            settings['capital'] = float(text.split()[1])
            save_json(SETTINGS_FILE, settings)
            send_telegram(f"✅ رأس المال: ${settings['capital']}")
        except: send_telegram("❌ /capital 10000")
    elif text.startswith('/risk '):
        try:
            settings['risk_percent'] = float(text.split()[1])
            save_json(SETTINGS_FILE, settings)
            send_telegram(f"✅ المخاطرة: {settings['risk_percent']}%")
        except: send_telegram("❌ /risk 2")
    else: send_telegram("🤔 اكتب /help")

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
            process_message(text, settings)
        save_last_update_id(max_update_id)
    except Exception as e: print(f"خطأ: {e}")

def run_autonomous_scan():
    print("🤖 بدء الفحص التلقائي...")
    
    # 1. التعلم من الأخطاء
    learned, lessons = deep_learn_from_mistakes()
    if learned:
        send_telegram("🧠 <b>تعلم جديد!</b>\n\n" + "\n".join([f"• {l}" for l in lessons]))
    
    # 2. التوقع كل 5 دقائق
    brain = get_brain()
    last_pred = brain.get('last_prediction_sent')
    minutes_since = 999 if not last_pred else (datetime.now(timezone.utc) - datetime.fromisoformat(last_pred)).total_seconds() / 60
    if minutes_since >= 5:
        pred = generate_prediction()
        if pred: send_telegram(pred)
    
    # 3. تنبيه الفرص القوية
    ind = calculate_indicators()
    if ind:
        brain = get_brain()
        if ind['rsi'] < brain['rsi_buy_threshold'] and ind['vix'] < 15 and ind['macd'] > ind['macd_signal']:
            send_telegram(f"🚨 <b>فرصة قوية!</b>\nRSI: {ind['rsi']} | VIX: {ind['vix']} | MACD: إيجابي")
    
    # 4. فحص النتائج تلقائياً (كل 6 ساعات)
    settings = get_settings()
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    current_hour = datetime.now(timezone.utc).hour
    
    if current_hour in [0, 6, 12, 18]:
        last_check = settings.get('last_results_check')
        check_key = f"{today_str}_{current_hour}"
        if last_check != check_key:
            print("📊 فحص نتائج التوقعات...")
            results = check_prediction_results()
            if results:
                report = generate_results_report(results)
                send_telegram(report)
                print(f"✅ تم فحص {len(results)} توقع")
            settings['last_results_check'] = check_key
            save_json(SETTINGS_FILE, settings)
    
    # 5. البحث عن عقود رخيصة (مرة يومياً)
    if settings.get('last_cheap_search') != today_str:
        print("💎 البحث عن عقود رخيصة...")
        cheap_msg = get_cheap_options_message()
        if cheap_msg and not cheap_msg.startswith(""):
            send_telegram(f"💎 <b>عقود رخيصة اليوم</b>\n\n{cheap_msg}")
        settings['last_cheap_search'] = today_str
        save_json(SETTINGS_FILE, settings)
    
    # 6. تقرير نهاية اليوم
    if current_hour == 19 and settings.get('last_daily_report') != today_str:
        print("📊 تقرير نهاية اليوم...")
        send_telegram(generate_daily_report())
        settings['last_daily_report'] = today_str
        save_json(SETTINGS_FILE, settings)
    
    # 7. 🆕 تقرير الإحصائيات الأسبوعي (كل أحد الساعة 20:00 UTC)
    now = datetime.now(timezone.utc)
    if now.weekday() == 6 and current_hour == 20:  # الأحد الساعة 20:00 UTC
        last_weekly = settings.get('last_weekly_stats')
        weekly_key = f"{today_str}_weekly"
        if last_weekly != weekly_key:
            print("📊 إرسال تقرير الإحصائيات الأسبوعي...")
            stats_msg = generate_full_stats()
            send_telegram(f"📊 <b>تقرير الإحصائيات الأسبوعي</b>\n\n{stats_msg}")
            settings['last_weekly_stats'] = weekly_key
            save_json(SETTINGS_FILE, settings)

if __name__ == '__main__':
    print("🚀 بدء بوت SPX...")
    handle_commands()
    run_autonomous_scan()
    print("✅ انتهى")
