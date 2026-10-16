import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import json
import time
import warnings
from datetime import datetime, timedelta, timezone
from collections import Counter
import shutil
warnings.filterwarnings('ignore')

# ==========================================
# ⚙️ إعدادات البوت
# ==========================================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'ضع_التوكن_هنا')
CHAT_ID = os.environ.get('CHAT_ID', 'ضع_الآيدي_هنا')

# ملفات البيانات
PORTFOLIO_FILE = 'options_portfolio.json'
SETTINGS_FILE = 'spx_settings.json'
PROCESSED_FILE = 'processed_spx.json'
BRAIN_FILE = 'bot_brain.json'
SIGNALS_FILE = 'signals_history.json'
NEWS_CACHE = 'news_cache.json'
PROMISING_OPTIONS_FILE = 'promising_options.json'
ALERTS_LOG = 'alerts_log.json'
REQUESTS_LOG = 'requests_log.json'
BACKUP_DIR = 'backups'

_indicators_cache = {'data': None, 'timestamp': 0}
_CACHE_DURATION = 300

# ==========================================
# 🕐 دوال الوقت
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
        'hour': saudi_now.hour, 'minute': saudi_now.minute,
        'weekday': saudi_now.weekday(),
        'weekday_name': ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد'][saudi_now.weekday()]
    }

# ==========================================
# 💾 دوال الملفات
# ==========================================
def load_json(fn, default=None):
    if default is None: default = {}
    try:
        with open(fn, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

def save_json(fn, data):
    with open(fn, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)

def create_backup():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backup_dir = f"{BACKUP_DIR}/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(backup_dir, exist_ok=True)
        
        files_to_backup = [PORTFOLIO_FILE, SETTINGS_FILE, BRAIN_FILE, SIGNALS_FILE, PROMISING_OPTIONS_FILE, ALERTS_LOG]
        
        for file in files_to_backup:
            if os.path.exists(file):
                shutil.copy(file, backup_dir)
        
        backups = sorted(os.listdir(BACKUP_DIR))
        if len(backups) > 5:
            for old_backup in backups[:-5]:
                shutil.rmtree(f"{BACKUP_DIR}/{old_backup}")
        
        print(f"✅ تم إنشاء نسخة احتياطية في {backup_dir}")
    except Exception as e:
        print(f"خطأ في النسخ الاحتياطي: {e}")

# ==========================================
# 🛡️ نظام Rate Limiting
# ==========================================
def rate_limit_check():
    requests_log = load_json(REQUESTS_LOG, {'requests': []})
    now = time.time()
    requests_log['requests'] = [r for r in requests_log['requests'] if now - r < 3600]
    
    if len(requests_log['requests']) >= 100:
        return False, "⚠️ تجاوزت الحد المسموح (100 طلب/ساعة)!"
    
    requests_log['requests'].append(now)
    save_json(REQUESTS_LOG, requests_log)
    return True, "✅"

# ==========================================
# 📊 المؤشرات الفنية المتقدمة
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
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1
        vix_data = yf.Ticker('^VIX').history(period='5d')
        
        # Multi-Timeframe Analysis
        hourly_data = yf.Ticker('SPY').history(period='5d', interval='1h')
        if len(hourly_data) > 14:
            hourly_delta = hourly_data['Close'].diff()
            hourly_gain = hourly_delta.where(hourly_delta > 0, 0).rolling(14).mean()
            hourly_loss = (-hourly_delta.where(hourly_delta < 0, 0)).rolling(14).mean()
            hourly_rsi = float(100 - (100 / (1 + (hourly_gain / hourly_loss))).iloc[-1])
        else:
            hourly_rsi = 50
        
        weekly_data = yf.Ticker('SPY').history(period='2y', interval='1wk')
        if len(weekly_data) > 14:
            weekly_delta = weekly_data['Close'].diff()
            weekly_gain = weekly_delta.where(weekly_delta > 0, 0).rolling(14).mean()
            weekly_loss = (-weekly_delta.where(weekly_delta < 0, 0)).rolling(14).mean()
            weekly_rsi = float(100 - (100 / (1 + (weekly_gain / weekly_loss))).iloc[-1])
        else:
            weekly_rsi = 50
        
        result = {
            'rsi': round(rsi, 2), 'macd': round(macd, 4), 'macd_signal': round(macd_signal, 4),
            'sma_20': round(sma_20, 2), 'sma_50': round(sma_50, 2), 'bb_upper': round(bb_upper, 2),
            'bb_lower': round(bb_lower, 2), 'volume_ratio': round(vol_ratio, 2),
            'price': round(float(close.iloc[-1]), 2), 'vix': round(float(vix_data['Close'].iloc[-1]), 2),
            'hourly_rsi': round(hourly_rsi, 2), 'weekly_rsi': round(weekly_rsi, 2),
            'trend': 'bullish' if close.iloc[-1] > sma_20 else 'bearish'
        }
        _indicators_cache = {'data': result, 'timestamp': now}
        return result
    except Exception as e:
        print(f"خطأ في calculate_indicators: {e}")
        return None

# ==========================================
# 📈 Order Flow Analysis
# ==========================================
def calculate_order_flow():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options:
            return "neutral", "⚠️ لا توجد بيانات", 0
        
        chain = ticker.option_chain(ticker.options[0])
        calls_vol = chain.calls['volume'].sum()
        puts_vol = chain.puts['volume'].sum()
        put_call_ratio = puts_vol / calls_vol if calls_vol > 0 else 0
        
        if put_call_ratio < 0.7: 
            return "bullish", "📈 تدفق شرائي قوي", round(put_call_ratio, 2)
        elif put_call_ratio > 1.2: 
            return "bearish", "📉 تدفق بيعي قوي", round(put_call_ratio, 2)
        else: 
            return "neutral", "⚖️ تدفق متوازن", round(put_call_ratio, 2)
    except Exception as e:
        print(f"خطأ في Order Flow: {e}")
        return "neutral", "️ خطأ في التحليل", 0

# ==========================================
# 🎯 Max Pain Calculation
# ==========================================
def calculate_max_pain():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options:
            return None
        
        chain = ticker.option_chain(ticker.options[0])
        current_price = float(yf.Ticker('SPY').history(period='1d')['Close'].iloc[-1])
        strikes = sorted(list(set(chain.calls['strike'].tolist())))
        
        max_pain_price = None
        min_total_value = float('inf')
        
        for strike in strikes:
            calls_oi = chain.calls[chain.calls['strike'] == strike]['openInterest'].sum()
            puts_oi = chain.puts[chain.puts['strike'] == strike]['openInterest'].sum()
            total_value = calls_oi * max(0, strike - current_price) + puts_oi * max(0, current_price - strike)
            
            if total_value < min_total_value:
                min_total_value = total_value
                max_pain_price = strike
        
        return round(max_pain_price, 2) if max_pain_price else current_price
    except Exception as e:
        print(f"خطأ في Max Pain: {e}")
        return None

# ==========================================
# 📊 IV Rank Calculation
# ==========================================
def calculate_iv_rank():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options:
            return 50
        
        chain = ticker.option_chain(ticker.options[0])
        current_iv = chain.calls['impliedVolatility'].mean() * 100
        
        historical_data = yf.Ticker('SPY').history(period='1y')
        historical_vol = historical_data['Close'].pct_change().std() * 252 * 100
        
        iv_rank = (current_iv / historical_vol) * 100 if historical_vol > 0 else 50
        return round(iv_rank, 2)
    except Exception as e:
        print(f"خطأ في IV Rank: {e}")
        return 50

# ==========================================
# 🕯️ Candlestick Patterns
# ==========================================
def detect_candlestick_patterns():
    try:
        data = yf.Ticker('SPY').history(period='1mo', interval='1d')
        if len(data) < 2:
            return [("Error", "️ بيانات غير كافية")]
        
        last = data.iloc[-1]
        prev = data.iloc[-2]
        body = abs(last['Close'] - last['Open'])
        shadow = last['High'] - last['Low']
        
        patterns = []
        
        if body < shadow * 0.1:
            patterns.append(("Doji", "️ تردد في السوق"))
        
        if last['Close'] > last['Open']:
            lower_shadow = min(last['Open'], last['Close']) - last['Low']
            if lower_shadow > body * 2:
                patterns.append(("Hammer", " انعكاس صاعد محتمل"))
        
        if last['Close'] < last['Open']:
            upper_shadow = last['High'] - max(last['Open'], last['Close'])
            if upper_shadow > body * 2:
                patterns.append(("Shooting Star", "⭐ انعكاس هابط محتمل"))
        
        if last['Close'] > last['Open'] and prev['Close'] < prev['Open']:
            if last['Open'] < prev['Close'] and last['Close'] > prev['Open']:
                patterns.append(("Bullish Engulfing", "📈 ابتلاع صاعد"))
        
        if last['Close'] < last['Open'] and prev['Close'] > prev['Open']:
            if last['Open'] > prev['Close'] and last['Close'] < prev['Open']:
                patterns.append(("Bearish Engulfing", "📉 ابتلاع هابط"))
        
        return patterns if patterns else [("Normal", "📊 لا توجد أنماط واضحة")]
    except Exception as e:
        print(f"خطأ في الأنماط: {e}")
        return [("Error", "⚠️ خطأ في التحليل")]

# ==========================================
#  Kelly Criterion
# ==========================================
def kelly_position_size(win_rate, win_loss_ratio):
    kelly_fraction = win_rate - ((1 - win_rate) / win_loss_ratio)
    return max(0.01, min(0.25, kelly_fraction / 2))

# ==========================================
# ️ Drawdown Protection
# ==========================================
def check_drawdown_protection(history):
    recent_signals = history.get('signals', [])[-10:]
    consecutive_losses = 0
    
    for signal in reversed(recent_signals):
        if signal.get('status') == 'wrong':
            consecutive_losses += 1
        else:
            break
    
    if consecutive_losses >= 3:
        return True, f"🚨 توقف! {consecutive_losses} خسائر متتالية"
    return False, "✅ يمكن الاستمرار"

# ==========================================
# ⏰ Time Decay Alert
# ==========================================
def check_time_decay(portfolio):
    alerts = []
    today = datetime.now()
    
    for pos in portfolio.get('positions', []):
        exp_date_str = pos.get('exp_date', '2026-12-31')
        try:
            exp_date = datetime.strptime(exp_date_str, '%Y-%m-%d')
            days_left = (exp_date - today).days
            
            if days_left <= 3:
                alerts.append(f"🔴 {pos['type']} ${pos['strike']} ينتهي خلال {days_left} يوم! خطر!")
            elif days_left <= 7:
                alerts.append(f"⚠️ {pos['type']} ${pos['strike']} ينتهي خلال {days_left} يوم")
            elif days_left <= 14:
                alerts.append(f"🟡 {pos['type']} ${pos['strike']} يقترب من الانتهاء ({days_left} يوم)")
        except:
            continue
    
    return alerts

# ==========================================
#  Backtesting
# ==========================================
def backtest_strategy(history, days=90):
    test_signals = [s for s in history.get('signals', []) if s.get('status') in ['correct', 'wrong']][-days:]
    
    total = len(test_signals)
    correct = sum(1 for s in test_signals if s.get('status') == 'correct')
    win_rate = correct / total if total > 0 else 0
    
    avg_win = sum(s.get('profit', 15) for s in test_signals if s.get('status') == 'correct') / max(1, correct)
    avg_loss = sum(s.get('loss', 10) for s in test_signals if s.get('status') == 'wrong') / max(1, total - correct)
    
    profit_factor = avg_win / max(0.01, avg_loss)
    
    cumulative = 0
    peak = 0
    max_drawdown = 0
    for s in test_signals:
        if s.get('status') == 'correct':
            cumulative += 15
        else:
            cumulative -= 10
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_drawdown = max(max_drawdown, drawdown)
    
    return {
        'win_rate': round(win_rate * 100, 2),
        'profit_factor': round(profit_factor, 2),
        'total_trades': total,
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'max_drawdown': round(max_drawdown, 2),
        'net_profit': round(cumulative, 2)
    }

# ==========================================
# 💼 Portfolio Optimization
# ==========================================
def optimize_portfolio(portfolio):
    positions = portfolio.get('positions', [])
    if not positions:
        return "📂 المحفظة فارغة"
    
    total_value = sum(p['entry'] * 100 * p['qty'] for p in positions)
    calls_value = sum(p['entry'] * 100 * p['qty'] for p in positions if p['type'] == 'CALL')
    puts_value = total_value - calls_value
    
    calls_ratio = calls_value / total_value if total_value > 0 else 0
    
    strikes = [p['strike'] for p in positions]
    unique_strikes = len(set(strikes))
    
    msg = f"💼 <b>تحليل المحفظة:</b>\n"
    msg += f"📊 القيمة الإجمالية: ${total_value:.2f}\n"
    msg += f"🟢 CALLs: {calls_ratio*100:.1f}% (${calls_value:.2f})\n"
    msg += f"🔴 PUTs: {(1-calls_ratio)*100:.1f}% (${puts_value:.2f})\n"
    msg += f"🎯 عدد العقود: {len(positions)}\n"
    msg += f"📈 أسعار الإضراب المختلفة: {unique_strikes}\n\n"
    
    if calls_ratio > 0.7:
        msg += f"️ <b>تحذير:</b> محفظتك مركزة في CALLs! وزع المخاطرة"
    elif calls_ratio < 0.3:
        msg += f"⚠️ <b>تحذير:</b> محفظتك مركزة في PUTs! وزع المخاطرة"
    elif unique_strikes < len(positions) * 0.5:
        msg += f"️ <b>تحذير:</b> عقودك مركزة في أسعار إضراب قليلة!"
    else:
        msg += f"✅ <b>التوزيع متوازن وجيد</b>"
    
    return msg

# ==========================================
# 🎯 دوال المعلومات الكاملة للعقود
# ==========================================
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
            'in_the_money': row.get('inTheMoney', False),
            'delta': row.get('delta', 0),
            'gamma': row.get('gamma', 0),
            'theta': row.get('theta', 0)
        }
    except Exception as e:
        print(f"خطأ في get_option_full_info: {e}")
        return None

# ==========================================
# 🦈 اكتشاف نشاط الحيتان المتقدم
# ==========================================
def detect_unusual_activity():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options: 
            print("لا توجد خيارات متاحة")
            return None
        
        expirations = ticker.options[:2]
        alerts = []
        
        for exp in expirations:
            chain = ticker.option_chain(exp)
            calls, puts = chain.calls, chain.puts
            
            avg_c = calls['volume'].mean() if len(calls) > 0 else 0
            avg_p = puts['volume'].mean() if len(puts) > 0 else 0
            
            if avg_c == 0 or avg_p == 0:
                continue
            
            unusual_calls = calls[calls['volume'] > avg_c * 2.5].head(3)
            unusual_puts = puts[puts['volume'] > avg_p * 2.5].head(3)
            
            for _, row in unusual_calls.iterrows():
                opt_info = get_option_full_info(row['strike'], 'CALL', exp)
                alerts.append({
                    'type': 'CALL', 
                    'strike': row['strike'], 
                    'volume': int(row['volume']), 
                    'ratio': round(row['volume']/avg_c, 1),
                    'symbol': opt_info['symbol'] if opt_info else 'N/A',
                    'exp': exp,
                    'dte_text': opt_info['dte_text'] if opt_info else 'N/A',
                    'oi': int(row.get('openInterest', 0))
                })
            
            for _, row in unusual_puts.iterrows():
                opt_info = get_option_full_info(row['strike'], 'PUT', exp)
                alerts.append({
                    'type': 'PUT', 
                    'strike': row['strike'], 
                    'volume': int(row['volume']), 
                    'ratio': round(row['volume']/avg_p, 1),
                    'symbol': opt_info['symbol'] if opt_info else 'N/A',
                    'exp': exp,
                    'dte_text': opt_info['dte_text'] if opt_info else 'N/A',
                    'oi': int(row.get('openInterest', 0))
                })
        
        return alerts[:6] if alerts else None
    except Exception as e:
        print(f"خطأ في detect_unusual_activity: {e}")
        return None

# ==========================================
# 💎 العثور على عقود واعدة
# ==========================================
def find_promising_cheap_options():
    try:
        ticker = yf.Ticker('SPY')
        if not ticker.options: 
            return None, "❌ لا توجد بيانات."
        
        ind = calculate_indicators()
        if not ind: 
            return None, "❌ لا يمكن جلب المؤشرات."
        
        brain = get_brain()
        expirations = ticker.options[:4] if len(ticker.options) >= 4 else ticker.options
        promising_calls, promising_puts = [], []
        current_price = ind['price']
        
        for exp in expirations:
            chain = ticker.option_chain(exp)
            
            for _, row in chain.calls.iterrows():
                if 0.05 < row['lastPrice'] < 5.0:
                    distance = ((row['strike'] - current_price) / current_price) * 100
                    if -2 < distance < 8:
                        confidence = 0
                        
                        if ind['rsi'] < brain.get('rsi_buy_threshold', 30): confidence += 35
                        elif ind['rsi'] > 60: confidence -= 25
                        
                        if ind['macd'] > ind['macd_signal']: confidence += 25
                        else: confidence -= 15
                        
                        if ind['vix'] < 20: confidence += 20
                        elif ind['vix'] > 30: confidence -= 30
                        
                        if row['volume'] > 500: confidence += 15
                        if row.get('delta', 0) > 0.3: confidence += 10
                        if row['lastPrice'] < 1.0: confidence += 10
                        
                        if confidence >= 20:
                            option_info = get_option_full_info(row['strike'], 'CALL', exp)
                            promising_calls.append({
                                'type': 'CALL', 'strike': row['strike'], 'price': row['lastPrice'], 'exp': exp,
                                'volume': int(row.get('volume', 0)), 'iv': row.get('impliedVolatility', 0) * 100, 
                                'delta': row.get('delta', 0), 'distance_pct': round(distance, 2), 
                                'confidence': confidence,
                                'reason': 'إشارة قوية + سعر رخيص' if confidence > 60 else 'ظروف مناسبة',
                                'option_symbol': option_info['symbol'] if option_info else 'N/A',
                                'dte_text': option_info['dte_text'] if option_info else 'N/A',
                                'open_interest': option_info['open_interest'] if option_info else 0,
                                'bid': option_info['bid'] if option_info else 0, 
                                'ask': option_info['ask'] if option_info else 0,
                                'spread': option_info['spread'] if option_info else 0,
                                'in_the_money': option_info['in_the_money'] if option_info else False
                            })

            for _, row in chain.puts.iterrows():
                if 0.05 < row['lastPrice'] < 5.0:
                    distance = ((current_price - row['strike']) / current_price) * 100
                    if -2 < distance < 8:
                        confidence = 0
                        
                        if ind['rsi'] > brain.get('rsi_sell_threshold', 70): confidence += 35
                        elif ind['rsi'] < 40: confidence -= 25
                        
                        if ind['macd'] < ind['macd_signal']: confidence += 25
                        else: confidence -= 15
                        
                        if ind['vix'] > 25: confidence += 20
                        elif ind['vix'] < 15: confidence -= 20
                        
                        if row['volume'] > 500: confidence += 15
                        if abs(row.get('delta', 0)) > 0.3: confidence += 10
                        if row['lastPrice'] < 1.0: confidence += 10
                        
                        if confidence >= 20:
                            option_info = get_option_full_info(row['strike'], 'PUT', exp)
                            promising_puts.append({
                                'type': 'PUT', 'strike': row['strike'], 'price': row['lastPrice'], 'exp': exp,
                                'volume': int(row.get('volume', 0)), 'iv': row.get('impliedVolatility', 0) * 100, 
                                'delta': row.get('delta', 0), 'distance_pct': round(distance, 2), 
                                'confidence': confidence,
                                'reason': 'إشارة قوية + سعر رخيص' if confidence > 60 else 'ظروف مناسبة',
                                'option_symbol': option_info['symbol'] if option_info else 'N/A',
                                'dte_text': option_info['dte_text'] if option_info else 'N/A',
                                'open_interest': option_info['open_interest'] if option_info else 0,
                                'bid': option_info['bid'] if option_info else 0, 
                                'ask': option_info['ask'] if option_info else 0,
                                'spread': option_info['spread'] if option_info else 0,
                                'in_the_money': option_info['in_the_money'] if option_info else False
                            })
        
        promising_calls.sort(key=lambda x: (x['confidence'], -x['price']), reverse=True)
        promising_puts.sort(key=lambda x: (x['confidence'], -x['price']), reverse=True)
        
        options_data = {
            'timestamp': datetime.now(timezone.utc).isoformat(), 
            'current_price': current_price, 
            'calls': promising_calls[:8], 
            'puts': promising_puts[:8], 
            'status': 'pending'
        }
        
        history = load_json(PROMISING_OPTIONS_FILE, {'options': []})
        history['options'].append(options_data)
        if len(history['options']) > 100: history['options'] = history['options'][-100:]
        save_json(PROMISING_OPTIONS_FILE, history)
        
        return options_data, None
    except Exception as e:
        print(f"خطأ في find_promising_cheap_options: {e}")
        return None, f"❌ خطأ: {str(e)}"

# ==========================================
# 📡 إرسال الرسائل
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
    except Exception as e:
        print(f"خطأ في الإرسال: {e}")
        return False

# ==========================================
# 🧠 دوال البوت الأساسية
# ==========================================
def get_settings():
    defaults = {
        'capital': 10000, 'risk_percent': 2.0, 
        'last_daily_report': None, 'last_results_check': None, 
        'last_weekly_stats': None, 'last_morning_report': None, 
        'last_pre_close': None
    }
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

# ==========================================
# 🎯 توليد التوقعات
# ==========================================
def generate_prediction():
    ind = calculate_indicators()
    if not ind: return None
    
    brain = get_brain()
    saudi_time = get_saudi_time()
    reasons, score = [], 0
    
    order_flow, order_msg, _ = calculate_order_flow()
    if order_flow == "bullish": score += 2; reasons.append(f"📈 {order_msg}")
    elif order_flow == "bearish": score -= 2; reasons.append(f"📉 {order_msg}")
    
    if ind['rsi'] < brain.get('rsi_buy_threshold', 30): score += 2; reasons.append(f"📈 RSI منخفض ({ind['rsi']})")
    elif ind['rsi'] > brain.get('rsi_sell_threshold', 70): score -= 2; reasons.append(f"📉 RSI مرتفع ({ind['rsi']})")
    
    if ind['macd'] > ind['macd_signal']: score += 1; reasons.append("✅ MACD إيجابي")
    else: score -= 1; reasons.append("❌ MACD سلبي")
    
    if ind['vix'] > brain.get('vix_fear_level', 25): score -= 2; reasons.append(f"⚠️ VIX مرتفع ({ind['vix']})")
    else: score += 1; reasons.append(f"🟢 VIX مستقر ({ind['vix']})")
    
    if ind.get('hourly_rsi', 50) < 30 and ind.get('weekly_rsi', 50) < 70:
        score += 1; reasons.append(" توافق الإطارات الزمنية")
    
    patterns = detect_candlestick_patterns()
    for pattern_name, pattern_desc in patterns[:1]:
        if "صاعد" in pattern_desc: score += 1
        elif "هابط" in pattern_desc: score -= 1
        reasons.append(f"🕯️ {pattern_desc}")
    
    if score >= 3: decision, advice = " شراء CALL", "💡 ادخل بـ 50% من حجمك مع وقف خسارة -20%"
    elif score <= -3: decision, advice = "🔴 شراء PUT", "💡 ادخل بحذر مع هدف ربح +30%"
    elif score >= 1: decision, advice = "🟡 CALL بحذر", "⚠️ انتظر تأكيداً إضافياً"
    elif score <= -1: decision, advice = "🟠 PUT بحذر", "⚠️ انتظر تأكيداً إضافياً"
    else: decision, advice = "⛔ لا تدخل", "💡 حافظ على رأس المال"
    
    signal = {
        'timestamp': datetime.now(timezone.utc).isoformat(), 
        'price': ind['price'], 
        'decision': decision, 
        'score': score, 
        'reasons': reasons, 
        'indicators': ind, 
        'status': 'pending'
    }
    
    history = load_json(SIGNALS_FILE, {'signals': []})
    history['signals'].append(signal)
    save_json(SIGNALS_FILE, history)
    
    brain['last_prediction_sent'] = datetime.now(timezone.utc).isoformat()
    save_brain(brain)
    
    msg = f"🤖 <b>توقع البوت الاحترافي</b>\n"
    msg += f"📅 {saudi_time['weekday']} {saudi_time['date']} | 🕐 {saudi_time['time']} السعودية\n\n"
    msg += f"🎯 <b>القرار:</b> {decision}\n"
    msg += f"💰 <b>السعر:</b> ${ind['price']}\n"
    msg += f"📊 <b>النقاط:</b> {score}\n\n"
    msg += f"<b>الأسباب:</b>\n" + "\n".join([f"• {r}" for r in reasons]) + f"\n\n{advice}\n"
    
    max_pain = calculate_max_pain()
    if max_pain:
        msg += f"\n🎯 <b>Max Pain:</b> ${max_pain}\n"
    
    iv_rank = calculate_iv_rank()
    msg += f"📈 <b>IV Rank:</b> {iv_rank}%\n"
    
    _, order_msg, pc_ratio = calculate_order_flow()
    msg += f"📊 <b>Put/Call Ratio:</b> {pc_ratio}\n"
    
    brain = get_brain()
    accuracy = (brain['correct_predictions']/max(1, brain['total_predictions']))*100
    msg += f"🎯 <b>دقة البوت:</b> {accuracy:.1f}%"
    
    return msg

# ==========================================
# 📊 التقرير الشامل
# ==========================================
def generate_comprehensive_report():
    ind = calculate_indicators()
    if not ind: return None
    
    saudi_time = get_saudi_time()
    
    msg = f"📊 <b>التقرير الشامل للسوق</b>\n"
    msg += f"📅 {saudi_time['weekday']} {saudi_time['date']} | 🕐 {saudi_time['time']} السعودية\n\n"
    
    msg += f" <b>SPY:</b> ${ind['price']}\n"
    msg += f" <b>RSI:</b> {ind['rsi']} (ساعي: {ind.get('hourly_rsi', 'N/A')})\n"
    msg += f"📉 <b>MACD:</b> {'إيجابي ✅' if ind['macd'] > ind['macd_signal'] else 'سلبي ❌'}\n"
    msg += f"😱 <b>VIX:</b> {ind['vix']}\n\n"
    
    order_flow, order_msg, pc_ratio = calculate_order_flow()
    emoji = "🟢" if order_flow == "bullish" else "" if order_flow == "bearish" else "🟡"
    msg += f"{emoji} <b>Order Flow:</b> {order_msg}\n"
    msg += f"📊 <b>Put/Call Ratio:</b> {pc_ratio}\n\n"
    
    max_pain = calculate_max_pain()
    if max_pain:
        distance = ((max_pain - ind['price']) / ind['price']) * 100
        msg += f"🎯 <b>Max Pain:</b> ${max_pain} ({distance:+.1f}%)\n\n"
    
    iv_rank = calculate_iv_rank()
    iv_status = "مرتفع 🔴" if iv_rank > 80 else "منخفض 🟢" if iv_rank < 20 else "متوسط 🟡"
    msg += f" <b>IV Rank:</b> {iv_rank}% ({iv_status})\n\n"
    
    patterns = detect_candlestick_patterns()
    msg += f"🕯️ <b>الأنماط:</b>\n"
    for name, desc in patterns[:3]:
        msg += f"• {desc}\n"
    msg += "\n"
    
    levels = calculate_support_resistance()
    if levels:
        msg += f"️ <b>المستويات:</b>\n"
        msg += f"🔴 مقاومة: {', '.join([f'${r}' for r in levels['resistance']])}\n"
        msg += f"🟢 دعم: {', '.join([f'${s}' for s in levels['support']])}\n\n"
    
    backtest = backtest_strategy(load_json(SIGNALS_FILE, {'signals': []}))
    msg += f" <b>أداء البوت (آخر 90 يوم):</b>\n"
    msg += f"• الصفقات: {backtest['total_trades']}\n"
    msg += f"• نسبة النجاح: {backtest['win_rate']}%\n"
    msg += f"• Profit Factor: {backtest['profit_factor']}\n"
    msg += f"• Max Drawdown: ${backtest['max_drawdown']}\n"
    msg += f"• صافي الربح: ${backtest['net_profit']}\n\n"
    
    if order_flow == "bullish" and ind['rsi'] < 40 and iv_rank < 50:
        recommendation = "🟢 <b>السوق مناسب للشراء</b>"
    elif order_flow == "bearish" and ind['rsi'] > 60 and iv_rank > 50:
        recommendation = "🔴 <b>السوق مناسب للبيع</b>"
    else:
        recommendation = "🟡 <b>انتظر إشارة أوضح</b>"
    
    msg += f"\n{recommendation}"
    
    return msg

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
# 🎮 معالجة الأوامر
# ==========================================
def handle_commands():
    last_update_id = load_json(PROCESSED_FILE, {'last_update_id': 0}).get('last_update_id', 0)
    
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id + 1}&limit=50&timeout=30", timeout=35).json()
        if not r.get('ok'): return
        updates = r.get('result', [])
        if not updates: return
        
        max_update_id = last_update_id
        
        for update in updates:
            update_id = update['update_id']
            if update_id > max_update_id: max_update_id = update_id
            
            message = update.get('message', {})
            if not message: continue
            
            text = message.get('text', '').strip()
            chat_id = str(message.get('chat', {}).get('id', ''))
            
            if chat_id != CHAT_ID: continue
            
            if text == '/help':
                msg = "🤖 <b>أوامر البوت الاحترافي:</b>\n\n"
                msg += "📊 <b>التقارير:</b>\n"
                msg += "/report - تقرير شامل\n"
                msg += "/predict - توقع البوت\n"
                msg += "/cheap - عقود واعدة\n"
                msg += "/whales - نشاط الحيتان\n\n"
                msg += "🧠 <b>التحليل:</b>\n"
                msg += "/orderflow - Order Flow\n"
                msg += "/maxpain - Max Pain\n"
                msg += "/ivrank - IV Rank\n"
                msg += "/patterns - الأنماط\n\n"
                msg += "💼 <b>المحفظة:</b>\n"
                msg += "/portfolio - عرض المحفظة\n"
                msg += "/optimize - تحسين المحفظة\n"
                msg += "/backtest - اختبار الاستراتيجية\n\n"
                msg += "⚙️ <b>إعدادات:</b>\n"
                msg += "/backup - نسخة احتياطية\n"
                msg += "/brain - عقل البوت\n"
                msg += "/stats - الإحصائيات"
                send_telegram(msg)
            
            elif text == '/report':
                send_telegram("📊 جاري إعداد التقرير الشامل...")
                report = generate_comprehensive_report()
                if report: send_telegram(report)
            
            elif text == '/orderflow':
                flow, msg, ratio = calculate_order_flow()
                send_telegram(f"📈 <b>Order Flow:</b>\n{msg}\n\n Put/Call Ratio: {ratio}")
            
            elif text == '/maxpain':
                mp = calculate_max_pain()
                ind = calculate_indicators()
                if mp and ind:
                    distance = ((mp - ind['price']) / ind['price']) * 100
                    send_telegram(f"🎯 <b>Max Pain:</b> ${mp}\n📊 السعر الحالي: ${ind['price']}\n📈 المسافة: {distance:+.1f}%")
                else:
                    send_telegram("❌ خطأ في الحساب")
            
            elif text == '/ivrank':
                iv = calculate_iv_rank()
                status = "مرتفع 🔴" if iv > 80 else "منخفض 🟢" if iv < 20 else "متوسط 🟡"
                send_telegram(f"📈 <b>IV Rank:</b> {iv}%\n{status}")
            
            elif text == '/patterns':
                patterns = detect_candlestick_patterns()
                msg = "️ <b>الأنماط الحالية:</b>\n\n"
                for name, desc in patterns:
                    msg += f"• {desc}\n"
                send_telegram(msg)
            
            elif text == '/backtest':
                send_telegram("📊 جاري الاختبار...")
                history = load_json(SIGNALS_FILE, {'signals': []})
                results = backtest_strategy(history)
                msg = f"📊 <b>نتائج الاختبار:</b>\n\n"
                msg += f"إجمالي الصفقات: {results['total_trades']}\n"
                msg += f"نسبة النجاح: {results['win_rate']}%\n"
                msg += f"Profit Factor: {results['profit_factor']}\n"
                msg += f"متوسط الربح: ${results['avg_win']}\n"
                msg += f"متوسط الخسارة: ${results['avg_loss']}\n"
                msg += f"Max Drawdown: ${results['max_drawdown']}\n"
                msg += f"صافي الربح: ${results['net_profit']}"
                send_telegram(msg)
            
            elif text == '/optimize':
                portfolio = load_json(PORTFOLIO_FILE, {'positions': []})
                msg = optimize_portfolio(portfolio)
                send_telegram(msg)
            
            elif text == '/backup':
                create_backup()
                send_telegram("✅ تم إنشاء نسخة احتياطية")
            
            elif text == '/predict':
                pred = generate_prediction()
                if pred: send_telegram(pred)
            
            elif text == '/cheap':
                send_telegram("🔍 جاري البحث عن عقود واعدة...")
                options_data, error = find_promising_cheap_options()
                if error:
                    send_telegram(error)
                elif options_data and (options_data['calls'] or options_data['puts']):
                    msg = f"💎 <b>عقود واعدة</b>\n"
                    msg += f"💰 SPY: ${options_data['current_price']}\n\n"
                    
                    if options_data['calls']:
                        msg += f"🟢 <b>CALLs ({len(options_data['calls'])}):</b>\n"
                        for i, c in enumerate(options_data['calls'][:5], 1):
                            msg += f"{i}. ${c['strike']} @ ${c['price']} | ثقة: {c['confidence']}%\n"
                            msg += f"   🆔 <code>{c.get('option_symbol', 'N/A')}</code>\n"
                            msg += f"    {c.get('dte_text', 'N/A')} | IV: {c['iv']:.1f}%\n\n"
                    
                    if options_data['puts']:
                        msg += f"🔴 <b>PUTs ({len(options_data['puts'])}):</b>\n"
                        for i, p in enumerate(options_data['puts'][:5], 1):
                            msg += f"{i}. ${p['strike']} @ ${p['price']} | ثقة: {p['confidence']}%\n"
                            msg += f"   🆔 <code>{p.get('option_symbol', 'N/A')}</code>\n"
                            msg += f"   📅 {p.get('dte_text', 'N/A')} | IV: {p['iv']:.1f}%\n\n"
                    
                    send_telegram(msg)
                else:
                    send_telegram("❌ لا توجد عقود واعدة حالياً")
            
            elif text == '/whales':
                whales = detect_unusual_activity()
                if whales:
                    msg = " <b>نشاط الحيتان:</b>\n\n"
                    for w in whales:
                        emoji = "🟢" if w['type'] == 'CALL' else "🔴"
                        msg += f"{emoji} <b>{w['type']} ${w['strike']}</b>\n"
                        msg += f"   🆔 <code>{w.get('symbol', 'N/A')}</code>\n"
                        msg += f"   📅 {w.get('exp', 'N/A')} ({w.get('dte_text', 'N/A')})\n"
                        msg += f"   📊 الحجم: {w['volume']} (×{w['ratio']}) | OI: {w.get('oi', 0)}\n\n"
                    send_telegram(msg)
                else:
                    send_telegram("🦈 لا نشاط غير طبيعي")
            
            elif text == '/portfolio':
                portfolio = load_json(PORTFOLIO_FILE, {'positions': []})
                if not portfolio['positions']:
                    send_telegram("📂 المحفظة فارغة")
                else:
                    msg = "💼 <b>المحفظة:</b>\n\n"
                    for i, pos in enumerate(portfolio['positions'], 1):
                        emoji = "🟢" if pos['type'] == 'CALL' else ""
                        msg += f"{i}. {emoji} {pos['type']} ${pos['strike']} @ ${pos['entry']} × {pos['qty']}\n"
                    send_telegram(msg)
            
            elif text == '/brain':
                brain = get_brain()
                acc = (brain['correct_predictions'] / max(1, brain['total_predictions'])) * 100
                msg = f"🧠 <b>عقل البوت:</b>\n\n"
                msg += f" الدقة: {acc:.1f}%\n"
                msg += f"✅ صحيح: {brain['correct_predictions']}\n"
                msg += f"❌ خاطئ: {brain['wrong_predictions']}\n"
                msg += f"📏 RSI شراء: < {brain['rsi_buy_threshold']}\n"
                msg += f" RSI بيع: > {brain['rsi_sell_threshold']}\n"
                msg += f"️ VIX: > {brain['vix_fear_level']}\n"
                
                if brain.get('learning_history'):
                    msg += f"\n📚 آخر درس:\n"
                    for lesson in brain['learning_history'][-1].get('lessons', [])[:2]:
                        msg += f"• {lesson}\n"
                
                send_telegram(msg)
            
            elif text == '/stats':
                brain = get_brain()
                history = load_json(SIGNALS_FILE, {'signals': []})
                backtest = backtest_strategy(history)
                
                msg = f"📊 <b>إحصائيات البوت:</b>\n\n"
                msg += f"<b>التوقعات:</b>\n"
                msg += f"• الإجمالي: {brain['total_predictions']}\n"
                msg += f"• الصحيح: {brain['correct_predictions']}\n"
                msg += f"• الخاطئ: {brain['wrong_predictions']}\n"
                msg += f"• الدقة: {(brain['correct_predictions']/max(1, brain['total_predictions']))*100:.1f}%\n\n"
                
                msg += f"<b>Backtest (90 يوم):</b>\n"
                msg += f"• الصفقات: {backtest['total_trades']}\n"
                msg += f"• Win Rate: {backtest['win_rate']}%\n"
                msg += f"• Profit Factor: {backtest['profit_factor']}\n"
                msg += f"• Net Profit: ${backtest['net_profit']}"
                
                send_telegram(msg)
            
            else:
                send_telegram("🤔 اكتب /help للأوامر المتاحة")
        
        save_json(PROCESSED_FILE, {'last_update_id': max_update_id})
    except Exception as e:
        print(f"خطأ في handle_commands: {e}")

# ==========================================
# 🔄 الفحص التلقائي
# ==========================================
def run_autonomous_scan():
    print(" بدء الفحص التلقائي...")
    saudi_time = get_saudi_time()
    print(f"🕐 الوقت: {saudi_time['time_with_seconds']}")
    
    if saudi_time['hour'] == 0 and saudi_time['minute'] < 5:
        create_backup()
    
    history = load_json(SIGNALS_FILE, {'signals': []})
    is_drawdown, drawdown_msg = check_drawdown_protection(history)
    if is_drawdown:
        send_telegram(f"🚨 {drawdown_msg}\n\n⚠️ البوت في وضع الحماية. انتظر حتى تتحسن النتائج.")
    
    portfolio = load_json(PORTFOLIO_FILE, {'positions': []})
    time_alerts = check_time_decay(portfolio)
    for alert in time_alerts:
        send_telegram(f"⏰ {alert}")
    
    brain = get_brain()
    last_pred = brain.get('last_prediction_sent')
    minutes_since = 999 if not last_pred else (datetime.now(timezone.utc) - datetime.fromisoformat(last_pred)).total_seconds() / 60
    
    if minutes_since >= 5:
        pred = generate_prediction()
        if pred:
            send_telegram(pred)
    
    ind = calculate_indicators()
    if ind:
        whales = detect_unusual_activity()
        if whales and len(whales) >= 3:
            msg = "🦈 <b>تنبيه: نشاط حيتان قوي!</b>\n\n"
            for w in whales[:3]:
                emoji = "🟢" if w['type'] == 'CALL' else "🔴"
                msg += f"{emoji} {w['type']} ${w['strike']} (×{w['ratio']})\n"
            send_telegram(msg)

# ==========================================
# 🚀 التشغيل الرئيسي
# ==========================================
if __name__ == '__main__':
    print("🚀 بدء تشغيل البوت الاحترافي...")
    saudi_time = get_saudi_time()
    print(f"🕐 الوقت: {saudi_time['time_with_seconds']}")
    
    create_backup()
    
    send_telegram(f"🟢 <b>تم تشغيل البوت الاحترافي</b>\n🕐 {saudi_time['time_with_seconds']}\n\n✅ جميع الميزات مفعلة")
    
    handle_commands()
    
    run_autonomous_scan()
    
    print("✅ انتهت الدورة. في انتظار التشغيل التالي...")
