import streamlit as st
import yfinance as yf
import ccxt
import pandas as pd
import numpy as np
import requests
from groq import Groq
from streamlit_lightweight_charts import renderLightweightCharts
from streamlit_autorefresh import st_autorefresh

# ==========================================
# إعدادات الصفحة
# ==========================================
st.set_page_config(page_title="AI SMC Trader", page_icon="📊", layout="wide")

# 🛑 ضع مفاتيحك هنا 🛑
TELEGRAM_TOKEN = "8959270070:AAGc1IxMWlc32bzBHoMX_7KcBRiJSsL0nxA"
TELEGRAM_CHAT_ID = "8619074139"
GROQ_API_KEY = "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"

ACCOUNT_BALANCE = 1000
RISK_PERCENT = 1.0

# تحديث تلقائي كل 60 ثانية
st_autorefresh(interval=60000, key="auto_refresh")

# ==========================================
# 1. جلب البيانات
# ==========================================
def fetch_data(symbol, timeframe='15m', limit=300):
    crypto_keywords = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE']
    is_crypto = any(c in symbol.upper() for c in crypto_keywords) or '/' in symbol
    
    if is_crypto:
        try:
            exchange = ccxt.binance()
            if '/' not in symbol: symbol = symbol.replace('USDT', '/USDT')
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df.set_index('time', inplace=True)
            return df
        except:
            symbol = symbol.replace('/', '-').replace('USDT', 'USD')
            is_crypto = False
            
    if not is_crypto:
        symbol_map = {'XAUUSD': 'GC=F', 'GOLD': 'GC=F', 'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X', 'SP500': '^GSPC', 'NAS100': '^NDX'}
        yf_symbol = symbol_map.get(symbol.upper(), symbol)
        period = "7d" if timeframe in ['1m', '5m', '15m', '30m'] else "1mo"
        try:
            df = yf.download(yf_symbol, interval=timeframe, period=period, progress=False)
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            df.columns = [str(c).lower() for c in df.columns]
            return df
        except:
            return pd.DataFrame()
    return df

# ==========================================
# 2. تحليل SMC
# ==========================================
def detect_zones(df, lookback=10):
    df = df.copy()
    df['swing_high'] = df['high'].rolling(window=lookback*2+1, center=True).max() == df['high']
    df['swing_low'] = df['low'].rolling(window=lookback*2+1, center=True).min() == df['low']
    
    zones = []
    last_sh, last_sl = np.nan, np.nan

    for i in range(lookback, len(df) - lookback):
        if df['swing_high'].iloc[i]: last_sh = df['high'].iloc[i]
        if df['swing_low'].iloc[i]: last_sl = df['low'].iloc[i]
        
        if not np.isnan(last_sh) and df['close'].iloc[i] > last_sh and df['close'].iloc[i-1] <= last_sh:
            for j in range(1, 20):
                if i-j >= 0 and df['close'].iloc[i-j] > df['open'].iloc[i-j]:
                    zones.append({
                        'type': 'SELL', 'time': df.index[i-j],
                        'top': float(df['high'].iloc[i-j]), 'bottom': float(df['low'].iloc[i-j]),
                        'status': 'Waiting'
                    })
                    break
        
        if not np.isnan(last_sl) and df['close'].iloc[i] < last_sl and df['close'].iloc[i-1] >= last_sl:
            for j in range(1, 20):
                if i-j >= 0 and df['close'].iloc[i-j] < df['open'].iloc[i-j]:
                    zones.append({
                        'type': 'BUY', 'time': df.index[i-j],
                        'top': float(df['high'].iloc[i-j]), 'bottom': float(df['low'].iloc[i-j]),
                        'status': 'Waiting'
                    })
                    break

    current_price = float(df['close'].iloc[-1])
    current_high = float(df['high'].iloc[-1])
    current_low = float(df['low'].iloc[-1])
    
    active_zones = []
    for z in zones[-8:]:
        if z['type'] == 'BUY':
            if current_low <= z['top'] and current_low >= z['bottom']:
                z['status'] = 'Touched'
                z['entry'] = z['top']
                z['sl'] = z['bottom']
                z['tp1'] = z['entry'] + (z['entry'] - z['sl']) * 1.5
                z['tp2'] = z['entry'] + (z['entry'] - z['sl']) * 2.5
            elif current_price < z['bottom']: z['status'] = 'Invalid'
        elif z['type'] == 'SELL':
            if current_high >= z['bottom'] and current_high <= z['top']:
                z['status'] = 'Touched'
                z['entry'] = z['bottom']
                z['sl'] = z['top']
                z['tp1'] = z['entry'] - (z['sl'] - z['entry']) * 1.5
                z['tp2'] = z['entry'] - (z['sl'] - z['entry']) * 2.5
            elif current_price > z['top']: z['status'] = 'Invalid'
                
        if z['status'] in ['Waiting', 'Touched']: active_zones.append(z)
    return df, active_zones

# ==========================================
# 3. إدارة المخاطر والذكاء الاصطناعي
# ==========================================
def calc_lot(entry, sl, symbol):
    risk_amount = ACCOUNT_BALANCE * (RISK_PERCENT / 100)
    distance = abs(entry - sl)
    if distance == 0: return 0.01
    pv = 100 if ('XAU' in symbol.upper() or 'GOLD' in symbol.upper()) else (1 if ('BTC' in symbol.upper() or 'ETH' in symbol.upper()) else 100000)
    lot = risk_amount / (distance * pv)
    max_lot = 0.01 if ('BTC' in symbol.upper() or 'ETH' in symbol.upper()) else 0.1
    return max(0.01, min(round(lot, 2), max_lot))

def ask_ai(setup, trend):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"خبير SMC. إشارة: {setup['type']} | دخول: {setup['entry']:.4f} | SL: {setup['sl']:.4f} | TP1: {setup['tp1']:.4f} | الاتجاه: {trend}. هل الصفقة قوية؟ أجب بـ نعم أو لا مع سبب مختصر."
    for m in ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "allam-2-7b", "openai/gpt-oss-20b"]:
        try:
            res = client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model=m)
            return res.choices[0].message.content
        except: continue
    return "تعذر الاتصال"

def send_tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        return True
    except: return False

# ==========================================
# 4. رسم شارت TradingView (مصحح بالكامل)
# ==========================================
def render_tv_chart(df, zones, symbol, timeframe):
    df_chart = df.reset_index()
    df_chart.rename(columns={df_chart.columns[0]: 'time'}, inplace=True)
    
    # 🛠️ تحويل الوقت إلى Unix timestamp بالثواني (إصلاح جذري)
    df_chart['time'] = pd.to_datetime(df_chart['time'])
    df_chart['time'] = df_chart['time'].astype('int64') // 10**9
    
    # 🛠️ تنظيف البيانات من التكرار والقيم الفارغة
    df_chart = df_chart.dropna(subset=['time', 'open', 'high', 'low', 'close'])
    df_chart = df_chart.drop_duplicates(subset=['time'], keep='last')
    df_chart = df_chart.sort_values('time').reset_index(drop=True)
    
    for col in ['open', 'high', 'low', 'close']:
        df_chart[col] = pd.to_numeric(df_chart[col], errors='coerce')
    df_chart = df_chart.dropna()
    
    candles = df_chart[['time', 'open', 'high', 'low', 'close']].to_dict('records')
    
    # بناء خطوط المناطق والصفقات
    price_lines = []
    for z in zones:
        if z['type'] == 'BUY':
            top_color = '#26a69a'
            label = "ZONE ACHAT"
        else:
            top_color = '#ef5350'
            label = "ZONE VENTE"
        
        price_lines.append({"price": z['top'], "color": top_color, "lineWidth": 2, "lineStyle": 2, "axisLabelVisible": True, "title": label})
        price_lines.append({"price": z['bottom'], "color": top_color, "lineWidth": 1, "lineStyle": 2, "axisLabelVisible": False})
        
        if z['status'] == 'Touched':
            price_lines.append({"price": z['entry'], "color": '#2962FF', "lineWidth": 2, "lineStyle": 0, "axisLabelVisible": True, "title": "ENTRY"})
            price_lines.append({"price": z['sl'], "color": '#FF1744', "lineWidth": 1, "lineStyle": 1, "axisLabelVisible": True, "title": "SL"})
            price_lines.append({"price": z['tp1'], "color": '#00E676', "lineWidth": 1, "lineStyle": 1, "axisLabelVisible": True, "title": "TP1"})
            price_lines.append({"price": z['tp2'], "color": '#00C853', "lineWidth": 1, "lineStyle": 1, "axisLabelVisible": True, "title": "TP2"})

    chartOptions = {
        "height": 500,
        "layout": {
            "background": {"type": 'solid', "color": '#131722'},
            "textColor": '#D9D9D9',
            "fontSize": 11,
        },
        "grid": {
            "vertLines": {"color": 'rgba(42, 46, 57, 0.5)', "style": 0},
            "horzLines": {"color": 'rgba(42, 46, 57, 0.5)', "style": 0},
        },
        "crosshair": {
            "mode": 1,
            "vertLine": {"color": '#758696', "width": 1, "style": 2, "labelBackgroundColor": '#2962FF'},
            "horzLine": {"color": '#758696', "width": 1, "style": 2, "labelBackgroundColor": '#2962FF'},
        },
        "rightPriceScale": {
            "borderColor": '#2B2B43',
            "scaleMargins": {"top": 0.1, "bottom": 0.1},
        },
        "timeScale": {
            "timeVisible": True,
            "secondsVisible": False,
            "borderColor": '#2B2B43',
            "rightOffset": 5,
            "barSpacing": 8,
            "minBarSpacing": 0.5,
        },
        "handleScroll": {
            "mouseWheel": True,
            "pressedMouseMove": True,
            "horzTouchDrag": True,
            "vertTouchDrag": True,
        },
        "handleScale": {
            "axisPressedMouseMove": True,
            "mouseWheel": True,
            "pinch": True,
        },
        "kineticScroll": {
            "touch": True,
            "mouse": False,
        },
    }
    
    seriesCandlestick = [{
        "type": 'Candlestick',
        "data": candles,
        "options": {
            "upColor": '#26a69a',
            "downColor": '#ef5350',
            "borderVisible": False,
            "wickUpColor": '#26a69a',
            "wickDownColor": '#ef5350',
            "priceLineVisible": True,
            "priceLineColor": '#787B86',
            "priceLineWidth": 1,
            "lastValueVisible": True,
        },
        "priceLines": price_lines
    }]
    
    renderLightweightCharts([{"chart": chartOptions, "series": seriesCandlestick}], f'tv_chart_{symbol}_{timeframe}')

# ==========================================
# 5. واجهة المستخدم
# ==========================================
st.title("📊 AI SMC Trader")

with st.sidebar:
    st.header("⚙️ اختيار السوق")
    symbol_choice = st.selectbox("اختر الأصل", ["BTC-USD", "ETH-USD", "GC=F (Or)", "EURUSD=X", "GBPUSD=X", "^GSPC (S&P500)"])
    symbol = symbol_choice.split(" ")[0]
    timeframe = st.selectbox("الفريم الزمني", ["5m", "15m", "30m", "1h", "4h", "1d"])
    st.divider()
    st.caption(f"💰 رأس المال: ${ACCOUNT_BALANCE} | المخاطرة: {RISK_PERCENT}%")
    st.caption("🔄 تحديث كل 60 ثانية")

try:
    df_ltf = fetch_data(symbol, timeframe)
    df_htf = fetch_data(symbol, '4h')
    
    if df_ltf.empty or df_htf.empty:
        st.error("❌ فشل جلب البيانات. جرب أصلاً آخر.")
        st.stop()
    
    ema = df_htf['close'].ewm(span=50).mean().iloc[-1]
    trend = "صعودي 📈" if df_htf['close'].iloc[-1] > ema else "هبوطي 📉"
    
    current_price = float(df_ltf['close'].iloc[-1])
    prev_price = float(df_ltf['close'].iloc[-2]) if len(df_ltf) > 1 else current_price
    change = current_price - prev_price
    change_pct = (change / prev_price) * 100 if prev_price != 0 else 0
    
    st.markdown(f"### {symbol} | {timeframe}")
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    col1.metric("💵 السعر الحالي", f"{current_price:.4f}", f"{change:+.4f} ({change_pct:+.2f}%)")
    col2.metric("اتجاه 4H", trend)
    col3.metric("آخر تحديث", pd.Timestamp.now().strftime("%H:%M:%S"))
    col4.metric("الحالة", "🟢 مباشر")
    
    df, active_zones = detect_zones(df_ltf)
    render_tv_chart(df, active_zones, symbol, timeframe)
    
    touched_zones = [z for z in active_zones if z['status'] == 'Touched']
    waiting_zones = [z for z in active_zones if z['status'] == 'Waiting']
    
    st.divider()
    
    if touched_zones:
        st.subheader("🚨 إشارات نشطة")
        for z in touched_zones:
            with st.container():
                if z['type'] == 'BUY': st.success(f"🟢 **{z['type']} - Zone Achat**")
                else: st.error(f"🔴 **{z['type']} - Zone Vente**")
                
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("💰 الدخول", f"{z['entry']:.4f}")
                col2.metric("🛑 SL", f"{z['sl']:.4f}")
                col3.metric("🎯 TP1", f"{z['tp1']:.4f}")
                col4.metric("🎯 TP2", f"{z['tp2']:.4f}")
                col5.metric("📊 اللوت", calc_lot(z['entry'], z['sl'], symbol))
                
                if st.button(f"🤖 استشارة AI", key=f"ai_{z['type']}_{z['time']}"):
                    with st.spinner("..."):
                        decision = ask_ai(z, trend)
                        st.info(f"🧠 {decision}")
                        if "نعم" in decision:
                            msg = f"🚨 إشارة {z['type']} على {symbol}\nدخول: {z['entry']:.4f}\nSL: {z['sl']:.4f}\nTP1: {z['tp1']:.4f}"
                            if send_tg(msg): st.success("✅ تم إرسال التنبيه!")
    
    if waiting_zones:
        st.subheader("⏳ مناطق في الانتظار")
        for z in waiting_zones:
            if z['type'] == 'BUY':
                st.info(f"🟢 **Zone Achat** | النطاق: {z['bottom']:.4f} - {z['top']:.4f}")
            else:
                st.info(f"🔴 **Zone Vente** | النطاق: {z['bottom']:.4f} - {z['top']:.4f}")

except Exception as e:
    st.error(f"❌ خطأ: {str(e)}")
