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

# --- إعدادات البوت (التوكن الجديد مدمج) ---
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
DAILY_REPORT_FILE = 'daily_report.json'

def load_json(fn, default=None):
    if default is None: default = {}
    try:
        with open(fn, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

def save_json(fn, data):
    with open(fn, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)

def get_settings():
    defaults = {'capital': 10000, 'risk_percent': 2.0, 'last_daily_report': None}
    settings = load_json(SETTINGS_FILE, defaults)
    for key in defaults:
        if key not in settings: settings[key] = defaults[key]
    return settings

# ==========================================
# 🧠 نظام العقل والتعلم المتقدم
# ==========================================
def get_brain():
    defaults = {
        'rsi_buy_threshold': 30,
        'rsi_sell_threshold': 70,
        'vix_fear_level': 25,
        'total_predictions': 0,
        'correct_predictions': 0,
        'wrong_predictions': 0,
        'last_adjustment_reason': 'لم يبدأ التعلم بعد',
        'last_prediction_sent': None,
        'mistake_patterns': {'rsi_too_early': 0, 'vix_ignored': 0, 'macd_false_signal': 0, 'news_conflict': 0},
        'learning_history': [],
        'best_strategy': 'CALL',
        'avoid_patterns': []
    }
    brain = load_json(BRAIN_FILE, defaults)
    for key in defaults:
        if key not in brain: brain[key] = defaults[key]
    return brain

def save_brain(brain):
    save_json(BRAIN_FILE, brain)

def deep_learn_from_mistakes():
    """تعلم عميق من الأخطاء مع تحليل الأنماط"""
    history = load_json(SIGNALS_FILE, {'signals': []})
    brain = get_brain()
    updated = False
    lessons = []
    
    recent_signals = [s for s in history['signals'] if s['status'] in ['correct', 'wrong']][-20:]
    if len(recent_signals) < 5:
        return False, []
    
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
        lessons.append("⚠️ الدقة منخفضة جداً. سأتجنب التداول عند VIX مرتفع")
        updated = True
    elif accuracy > 0.7:
        lessons.append("✅ أداء ممتاز! سأزيد الثقة في التوقعات")
        updated = True
    
    if lessons:
        brain['learning_history'].append({
            'date': datetime.now(timezone.utc).isoformat(),
            'accuracy': round(accuracy * 100, 1),
            'lessons': lessons
        })
        if len(brain['learning_history']) > 50:
            brain['learning_history'] = brain['learning_history'][-50:]
    
    if updated:
        save_brain(brain)
    
    return updated, lessons

# ==========================================
# 📊 حساب المؤشرات الفنية
# ==========================================
def calculate_indicators():
    try:
        data = yf.Ticker('SPY').history(period='6mo', interval='1d')
        if len(data) < 50: return None
        
        close, high, low, volume = data['Close'], data['High'], data['Low'], data['Volume']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = float(100 - (100 / (1 + rs)).iloc[-1])
        
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = float(ema12.iloc[-1] - ema26.iloc[-1])
        macd_signal = float((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1])
        
        sma_20 = float(close.rolling(20).mean().iloc[-1])
        sma_50 = float(close.rolling(50).mean().iloc[-1])
        bb_std = close.rolling(20).std().iloc[-1]
        bb_upper = sma_20 + (bb_std * 2)
        bb_lower = sma_20 - (bb_std * 2)
        
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1
        
        vix_data = yf.Ticker('^VIX').history(period='5d')
        
        return {
            'rsi': round(rsi, 2), 'macd': round(macd, 4), 'macd_signal': round(macd_signal, 4),
            'sma_20': round(sma_20, 2), 'sma_50': round(sma_50, 2), 'bb_upper': round(bb_upper, 2),
            'bb_lower': round(bb_lower, 2), 'atr': round(atr, 2), 'volume_ratio': round(vol_ratio, 2),
            'price': round(float(close.iloc[-1]), 2), 'vix': round(float(vix_data['Close'].iloc[-1]), 2)
        }
    except Exception as e:
        print(f"خطأ في الحسابات: {e}")
        return None

# ==========================================
# 🗺️ خريطة الدعم والمقاومة
# ==========================================
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

# ==========================================
# 📰 ماسح مشاعر الأخبار
# ==========================================
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

# ==========================================
# 📅 تقويم الأحداث الاقتصادية
# ==========================================
def get_economic_events():
    try:
        cache = load_json(EVENTS_CACHE, {'last_update': None, 'events': []})
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if cache.get('last_update') == today: return cache['events']
        
        # أحداث اقتصادية مهمة (مثال ثابت يمكن تحديثه)
        events = [
            {'date': '2024-01-31', 'event': 'FOMC Decision', 'impact': 'high'},
            {'date': '2024-02-02', 'event': 'Non-Farm Payrolls', 'impact': 'high'},
            {'date': '2024-02-13', 'event': 'CPI Data', 'impact': 'high'}
        ]
        upcoming = [e for e in events if e['date'] >= today][:3]
        save_json(EVENTS_CACHE, {'last_update': today, 'events': upcoming})
        return upcoming
    except: return []

# ==========================================
# 🦈 كشف نشاط الحيتان
# ==========================================
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

# ==========================================
# 💡 اقتراح الاستراتيجيات المتقدمة
# ==========================================
def suggest_strategy(ind, brain):
    if ind['vix'] > 25 and abs(ind['rsi'] - 50) < 20: 
        return {'name': 'Iron Condor', 'desc': 'سوق متذبذب مع خوف مرتفع', 'action': 'بيع Call و Put بعيداً عن السعر'}
    if ind['rsi'] < 40 and ind['macd'] > ind['macd_signal'] and ind['vix'] < 20: 
        return {'name': 'Bull Call Spread', 'desc': 'اتجاه صاعد مع زخم إيجابي', 'action': 'شراء Call عند السعر + بيع Call أعلى'}
    if ind['rsi'] > 60 and ind['macd'] < ind['macd_signal']: 
        return {'name': 'Bear Put Spread', 'desc': 'اتجاه هابط مع تشبع شرائي', 'action': 'شراء Put عند السعر + بيع Put أدنى'}
    if ind['rsi'] < 25 and ind['vix'] < 15: 
        return {'name': 'Long Call', 'desc': 'فرصة شراء قوية مع خوف منخفض', 'action': 'شراء Call مباشرة'}
    if ind['rsi'] > 75 and ind['vix'] > 20: 
        return {'name': 'Long Put', 'desc': 'فرصة بيع قوية مع خوف مرتفع', 'action': 'شراء Put مباشرة'}
    return {'name': 'انتظار', 'desc': 'السوق غير واضح', 'action': 'انتظر إشارة أوضح'}

# ==========================================
# 📈 حاسبة الربح والخسارة الحية
# ==========================================
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
            results.append({
                'type': pos['type'], 'strike': pos['strike'], 'entry': pos['entry'], 
                'current': round(current_opt, 2), 'pnl': round(pnl, 2), 
                'pnl_pct': round(((current_opt - pos['entry']) / pos['entry']) * 100, 1), 'qty': pos['qty']
            })
        except: continue
    return {'positions': results, 'total_pnl': round(total_pnl, 2)}

# ==========================================
# 🎯 توليد التوقع مع النصائح الصريحة
# ==========================================
def generate_prediction():
    ind = calculate_indicators()
    if not ind: return None
    
    brain, levels = get_brain(), calculate_support_resistance()
    sentiment, articles = analyze_news_sentiment()
    strategy = suggest_strategy(ind, brain)
    reasons, score = [], 0
    
    if ind['rsi'] < brain['rsi_buy_threshold']: score += 2; reasons.append(f"📈 RSI منخفض جداً ({ind['rsi']}), فرصة ارتداد")
    elif ind['rsi'] > brain['rsi_sell_threshold']: score -= 2; reasons.append(f"📉 RSI مرتفع جداً ({ind['rsi']}), احتمال تصحيح")
    
    if ind['macd'] > ind['macd_signal']: score += 1; reasons.append("✅ MACD إيجابي (زخم صعودي)")
    else: score -= 1; reasons.append("❌ MACD سلبي (زخم هبوطي)")
    
    if ind['vix'] > brain['vix_fear_level']: score -= 2; reasons.append(f"⚠️ VIX مرتفع ({ind['vix']}), تجنب الشراء")
    else: score += 1; reasons.append(f"🟢 VIX مستقر ({ind['vix']})")
    
    if ind['price'] > ind['sma_20']: score += 1; reasons.append(f"📊 السعر فوق SMA20 ({ind['sma_20']})")
    
    if sentiment == 'positive': score += 1; reasons.append("📰 أخبار إيجابية تدعم الصعود")
    elif sentiment == 'negative': score -= 1; reasons.append("📰 أخبار سلبية تضغط على السوق")
    
    if score >= 3: decision, advice = "🟢 شراء عقد CALL", "💡 نصيحتي: ادخل بـ 50% من حجمك المعتاد وضع وقف خسارة عند -20%"
    elif score <= -3: decision, advice = "🔴 شراء عقد PUT", "💡 نصيحتي: ادخل بحذر وضع هدف ربح عند +30%"
    elif score >= 1: decision, advice = "🟡 CALL بحذر", "⚠️ نصيحتي: السوق ليس مثالياً. انتظر تأكيداً إضافياً"
    elif score <= -1: decision, advice = "🟠 PUT بحذر", "⚠️ نصيحتي: السوق ليس مثالياً. انتظر تأكيداً إضافياً"
    else: decision, advice = "⛔ لا تدخل الآن", "💡 نصيحتي: السوق غير واضح. حافظ على رأس المال"
    
    signal = {
        'timestamp': datetime.now(timezone.utc).isoformat(), 'price': ind['price'], 'decision': decision, 
        'score': score, 'reasons': reasons, 'indicators': ind, 'strategy': strategy['name'], 'status': 'pending'
    }
    history = load_json(SIGNALS_FILE, {'signals': []})
    history['signals'].append(signal)
    save_json(SIGNALS_FILE, history)
    
    brain['last_prediction_sent'] = datetime.now(timezone.utc).isoformat()
    save_brain(brain)
    
    msg = f"🤖 <b>توقع البوت الذكي</b>\n📅 {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
    msg += f"🎯 <b>القرار:</b> {decision}\n💰 <b>السعر:</b> ${ind['price']}\n🧠 <b>الاستراتيجية:</b> {strategy['name']}\n\n"
    msg += f"<b>الأسباب:</b>\n" + "\n".join([f"• {r}" for r in reasons]) + f"\n\n{advice}\n"
    
    if levels:
        msg += f"🗺️ <b>المستويات:</b>\n• مقاومة: {', '.join([f'${r}' for r in levels['resistance']])}\n• دعم: {', '.join([f'${s}' for s in levels['support']])}\n"
    if articles:
        msg += f"📰 <b>آخر الأخبار:</b>\n" + "\n".join([f"• {art['title'][:50]}..." for art in articles[:2]]) + "\n"
    
    msg += f"🎯 <b>دقة البوت:</b> {(brain['correct_predictions']/max(1, brain['total_predictions']))*100:.1f}%"
    return msg

# ==========================================
# 📊 تقرير نهاية اليوم
# ==========================================
def generate_daily_report():
    brain = get_brain()
    history = load_json(SIGNALS_FILE, {'signals': []})
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    today_signals = [s for s in history['signals'] if s['timestamp'].startswith(today)]
    correct = sum(1 for s in today_signals if s['status'] == 'correct')
    wrong = sum(1 for s in today_signals if s['status'] == 'wrong')
    pending = sum(1 for s in today_signals if s['status'] == 'pending')
    
    pnl_data = calculate_live_pnl()
    
    msg = f"📊 <b>تقرير نهاية اليوم</b>\n📅 {today}\n\n"
    msg += f"<b>أداء البوت اليوم:</b>\n✅ صحيح: {correct}\n❌ خاطئ: {wrong}\n⏳ معلق: {pending}\n"
    
    if correct + wrong > 0:
        msg += f"🎯 الدقة: {(correct / (correct + wrong)) * 100:.1f}%\n"
    
    if pnl_data:
        msg += f"\n{'🟢' if pnl_data['total_pnl'] >= 0 else '🔴'} <b>الربح/الخسارة:</b> ${pnl_data['total_pnl']}\n"
    
    if brain['learning_history']:
        msg += f"\n📚 <b>آخر درس تعلمه البوت:</b>\n" + "\n".join([f"• {l}" for l in brain['learning_history'][-1].get('lessons', [])])
    
    return msg

# ==========================================
# 📡 إرسال الرسائل ومعالجة الأوامر
# ==========================================
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

def get_processed():
    data = load_json(PROCESSED_FILE, {'processed': [], 'date': ''})
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if data.get('date') != today:
        data = {'processed': [], 'date': today}
        save_json(PROCESSED_FILE, data)
    return data.get('processed', [])

def add_processed(uid):
    data = load_json(PROCESSED_FILE, {'processed': [], 'date': ''})
    if uid not in data['processed']:
        data['processed'].append(uid)
        if len(data['processed']) > 500: data['processed'] = data['processed'][-250:]
        save_json(PROCESSED_FILE, data)

def get_option_data(strike):
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options: return "❌ لا توجد بيانات خيارات."
        chain = ticker.option_chain(ticker.options[0])
        c = chain.calls.iloc[(chain.calls['strike'] - strike).abs().argsort()[:1].item()]
        p = chain.puts.iloc[(chain.puts['strike'] - strike).abs().argsort()[:1].item()]
        return f"🔥 <b>بيانات العقد: Strike {strike}</b>\n📅 الانتهاء: {ticker.options[0]}\n\n🟢 <b>CALL:</b> ${c['lastPrice']:.2f} | IV: {c['impliedVolatility']*100:.1f}% | Delta: {c['delta']:.2f} | Vol: {int(c['volume'])}\n🔴 <b>PUT:</b> ${p['lastPrice']:.2f} | IV: {p['impliedVolatility']*100:.1f}% | Delta: {p['delta']:.2f} | Vol: {int(p['volume'])}"
    except Exception as e: return f"❌ خطأ: {str(e)}"

def add_option_position(opt_type, strike, entry_price, qty):
    settings = get_settings()
    data = load_json(PORTFOLIO_FILE, {'positions': []})
    total_cost = entry_price * 100 * qty
    if total_cost > (settings['capital'] * (settings['risk_percent'] / 100)):
        return False, f"⚠️ التكلفة (${total_cost:.2f}) تتجاوز المخاطرة المسموحة!"
    data['positions'].append({'type': opt_type.upper(), 'strike': strike, 'entry': entry_price, 'qty': qty, 'date': datetime.now(timezone.utc).strftime('%Y-%m-%d')})
    save_json(PORTFOLIO_FILE, data)
    return True, f"✅ تم تسجيل {opt_type.upper()} Strike {strike} بسعر ${entry_price} × {qty}"

def get_portfolio():
    data = load_json(PORTFOLIO_FILE, {'positions': []})
    if not data['positions']: return "📂 <b>المحفظة فارغة</b>"
    pnl_data = calculate_live_pnl()
    msg = "💼 <b>المحفظة النشطة</b>\n━━━━━━━━━━━━━━━\n"
    for i, pos in enumerate(data['positions'], 1):
        cost = pos['entry'] * 100 * pos['qty']
        emoji = "🟢" if pos['type'] == 'CALL' else "🔴"
        pnl_info = ""
        if pnl_data and i <= len(pnl_data['positions']):
            p = pnl_data['positions'][i-1]
            pnl_info = f" | {'🟢' if p['pnl'] >= 0 else '🔴'} P&L: ${p['pnl']} ({p['pnl_pct']}%)"
        msg += f"{i}. {emoji} <b>{pos['type']} {pos['strike']}</b> | ${pos['entry']} × {pos['qty']} (${cost:.2f}){pnl_info}\n"
    if pnl_data:
        msg += f"\n━━━━━━━━━━━━━━━\n{'🟢' if pnl_data['total_pnl'] >= 0 else '🔴'} <b>إجمالي P&L:</b> ${pnl_data['total_pnl']}"
    return msg

def remove_position(index):
    data = load_json(PORTFOLIO_FILE, {'positions': []})
    if 0 < index <= len(data['positions']):
        removed = data['positions'].pop(index - 1)
        save_json(PORTFOLIO_FILE, data)
        return True, f"✅ تم حذف: {removed['type']} Strike {removed['strike']}"
    return False, "❌ رقم غير صحيح"

def process_message(text, settings):
    text_lower = text.lower().strip()
    if text == '/help':
        msg = "🤖 <b>أوامر بوت SPX الجبار:</b>\n\n🧠 <b>التحليل:</b>\n/predict - توقع مع نصيحة\n/brain - عرض عقل البوت\n/spx - لوحة التحكم\n\n🔥 <b>الخيارات:</b>\n/option [strike]\n/buy_call [strike] [price] [qty]\n/buy_put [strike] [price] [qty]\n/portfolio - المحفظة مع P&L\n/close [رقم]\n\n📊 <b>متقدم:</b>\n/levels - الدعم والمقاومة\n/sentiment - مشاعر الأخبار\n/whales - نشاط الحيتان\n/strategy - استراتيجية مقترحة\n/events - الأحداث الاقتصادية\n/daily - تقرير نهاية اليوم"
        send_telegram(msg)
    elif text == '/predict':
        send_telegram("🧠 جاري التحليل...")
        pred = generate_prediction()
        if pred: send_telegram(pred)
    elif text == '/brain':
        brain = get_brain()
        acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
        msg = f"🧠 <b>عقل البوت:</b>\n🎯 الدقة: {acc:.1f}%\n📏 RSI شراء: < {brain['rsi_buy_threshold']}\n📏 RSI بيع: > {brain['rsi_sell_threshold']}\n🛡️ VIX خوف: > {brain['vix_fear_level']}\n\n<b>أنماط الأخطاء:</b>\n"
        for p, c in brain['mistake_patterns'].items(): msg += f"• {p}: {c}\n"
        msg += f"\n📚 <b>آخر درس:</b>\n{brain['last_adjustment_reason']}"
        send_telegram(msg)
    elif text == '/spx':
        ind = calculate_indicators()
        if ind: send_telegram(f"📊 <b>SPX/SPY</b>\n💰 السعر: ${ind['price']}\n📈 RSI: {ind['rsi']}\n📉 MACD: {ind['macd']}\n😱 VIX: {ind['vix']}\n📊 SMA50: ${ind['sma_50']}\n📦 Volume: {ind['volume_ratio']}x")
    elif text == '/levels':
        levels = calculate_support_resistance()
        if levels: send_telegram(f"🗺️ <b>الدعم والمقاومة</b>\n💰 الحالي: ${levels['current_price']}\n\n🔴 <b>مقاومة:</b>\n" + "\n".join([f"• ${r}" for r in levels['resistance']]) + f"\n\n🟢 <b>دعم:</b>\n" + "\n".join([f"• ${s}" for s in levels['support']]))
    elif text == '/sentiment':
        sent, arts = analyze_news_sentiment()
        emoji = "🟢" if sent == 'positive' else "🔴" if sent == 'negative' else "🟡"
        send_telegram(f"📰 <b>مشاعر الأخبار: {sent.upper()}</b> {emoji}\n\n" + "\n".join([f"• {a['title'][:60]}..." for a in arts]))
    elif text == '/whales':
        whales = detect_unusual_activity()
        if whales: send_telegram("🦈 <b>نشاط الحيتان</b>\n\n" + "\n\n".join([f"{'🟢' if w['type'] == 'CALL' else '🔴'} <b>{w['type']} Strike {w['strike']}</b>\n• الحجم: {w['volume']} (×{w['ratio']} من المعدل)" for w in whales]))
        else: send_telegram("🦈 لا يوجد نشاط غير طبيعي حالياً")
    elif text == '/strategy':
        ind = calculate_indicators()
        if ind:
            strat = suggest_strategy(ind, get_brain())
            send_telegram(f"🎯 <b>الاستراتيجية: {strat['name']}</b>\n\n📝 {strat['desc']}\n\n⚡ <b>الإجراء:</b>\n{strat['action']}")
    elif text == '/events':
        events = get_economic_events()
        if events: send_telegram("📅 <b>الأحداث الاقتصادية</b>\n\n" + "\n".join([f"{'🔴' if e['impact']=='high' else ''} <b>{e['date']}</b>: {e['event']}" for e in events]))
        else: send_telegram("📅 لا توجد أحداث مهمة قريبة")
    elif text == '/daily':
        send_telegram("📊 جاري إعداد التقرير...")
        send_telegram(generate_daily_report())
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
    processed = get_processed()
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset=0&limit=50", timeout=10).json()
        if not r.get('ok'): return
        for update in r.get('result', []):
            uid = update['update_id']
            if uid in processed: continue
            msg = update.get('message', {})
            if not msg: add_processed(uid); continue
            sender = msg.get('from', {})
            if sender.get('is_bot', False) or (bot_id and sender.get('id') == bot_id): add_processed(uid); continue
            text = msg.get('text', '').strip()
            if str(msg.get('chat', {}).get('id', '')) != CHAT_ID: add_processed(uid); continue
            process_message(text, get_settings())
            add_processed(uid)
    except Exception as e: print(f"خطأ: {e}")

def run_autonomous_scan():
    print("🤖 بدء الفحص التلقائي...")
    learned, lessons = deep_learn_from_mistakes()
    if learned:
        send_telegram("🧠 <b>البوت تعلم دروساً جديدة!</b>\n\n" + "\n".join([f"• {l}" for l in lessons]))
    
    brain = get_brain()
    last_pred = brain.get('last_prediction_sent')
    minutes_since = 999 if not last_pred else (datetime.now(timezone.utc) - datetime.fromisoformat(last_pred)).total_seconds() / 60
    
    if minutes_since >= 5:
        print("📊 توليد توقع جديد...")
        pred = generate_prediction()
        if pred:
            send_telegram(pred)
            print("✅ تم إرسال التوقع")
    
    ind = calculate_indicators()
    if ind:
        brain = get_brain()
        if ind['rsi'] < brain['rsi_buy_threshold'] and ind['vix'] < 15 and ind['macd'] > ind['macd_signal']:
            send_telegram(f"🚨 <b>فرصة قوية جداً!</b>\n\nRSI: {ind['rsi']} | VIX: {ind['vix']} | MACD: إيجابي")
            
    # تقرير نهاية اليوم (مرة واحدة يومياً الساعة 10 مساءً بتوقيت السعودية / 7 مساءً UTC)
    settings = get_settings()
    current_hour = datetime.now(timezone.utc).hour
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if current_hour == 19 and settings.get('last_daily_report') != today_str:
        print("📊 إرسال تقرير نهاية اليوم...")
        send_telegram(generate_daily_report())
        settings['last_daily_report'] = today_str
        save_json(SETTINGS_FILE, settings)

if __name__ == '__main__':
    print("🚀 بدء بوت SPX الجبار المتكامل...")
    handle_commands()
    run_autonomous_scan()
    print("✅ انتهى")
