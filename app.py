import streamlit as st
import yfinance as yf
import ccxt
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
from groq import Groq

st.set_page_config(page_title="AI SMC Trader", page_icon="🤖", layout="wide")

# 🛑 ضع مفاتيحك هنا 🛑
TELEGRAM_TOKEN = "8959270070:AAGc1IxMWlc32bzBHoMX_7KcBRiJSsL0nxA"
TELEGRAM_CHAT_ID = "8619074139"
GROQ_API_KEY = "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"

ACCOUNT_BALANCE = 1000
RISK_PERCENT = 1.0

def fetch_data(symbol, timeframe='15m', limit=500):
    crypto_keywords = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE']
    is_crypto = any(c in symbol.upper() for c in crypto_keywords) or '/' in symbol
    if is_crypto:
        try:
            exchange = ccxt.binance()
            if '/' not in symbol: symbol = symbol.replace('USDT', '/USDT')
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
        except:
            symbol = symbol.replace('/', '-').replace('USDT', 'USD')
            is_crypto = False
    if not is_crypto:
        symbol_map = {'XAUUSD': 'GC=F', 'GOLD': 'GC=F', 'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X', 'SP500': '^GSPC', 'NAS100': '^NDX', 'BTC-USD': 'BTC-USD'}
        yf_symbol = symbol_map.get(symbol.upper(), symbol)
        period = "7d" if timeframe in ['1m', '5m', '15m', '30m'] else "1mo"
        df = yf.download(yf_symbol, interval=timeframe, period=period, progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
    df.dropna(inplace=True)
    return df

def analyze_smc(df, lookback=10): # زيادة lookback لتقليل الإشارات الكاذبة
    df = df.copy()
    df['swing_high'] = df['high'].rolling(window=lookback*2+1, center=True).max() == df['high']
    df['swing_low'] = df['low'].rolling(window=lookback*2+1, center=True).min() == df['low']
    setups, sweeps = [], []
    last_sh, last_sl = np.nan, np.nan
    for i in range(lookback, len(df) - lookback):
        if df['swing_high'].iloc[i]: last_sh = df['high'].iloc[i]
        if df['swing_low'].iloc[i]: last_sl = df['low'].iloc[i]
        
        if not np.isnan(last_sh) and df['high'].iloc[i] > last_sh and df['close'].iloc[i] < last_sh:
            sweeps.append({'time': df.index[i], 'price': df['high'].iloc[i], 'type': 'Sweep High'})
        if not np.isnan(last_sl) and df['low'].iloc[i] < last_sl and df['close'].iloc[i] > last_sl:
            sweeps.append({'time': df.index[i], 'price': df['low'].iloc[i], 'type': 'Sweep Low'})
        
        # كشف BUY
        if not np.isnan(last_sh) and df['close'].iloc[i] > last_sh and df['close'].iloc[i-1] <= last_sh:
            for j in range(1, 20):
                if i-j >= 0 and df['close'].iloc[i-j] < df['open'].iloc[i-j]:
                    ot, ob = df['high'].iloc[i-j], df['low'].iloc[i-j]
                    # شرط الحد الأدنى لمسافة وقف الخسارة
                    if abs(ot - ob) > 0.0002: 
                        setups.append({'type': 'BUY', 'time': df.index[i-j], 'top': ot, 'bottom': ob,
                                       'entry': ot, 'sl': ob, 'tp1': ot+(ot-ob)*1.5, 'tp2': ot+(ot-ob)*2.5})
                    break
        
        # كشف SELL
        if not np.isnan(last_sl) and df['close'].iloc[i] < last_sl and df['close'].iloc[i-1] >= last_sl:
            for j in range(1, 20):
                if i-j >= 0 and df['close'].iloc[i-j] > df['open'].iloc[i-j]:
                    ot, ob = df['high'].iloc[i-j], df['low'].iloc[i-j]
                    if abs(ot - ob) > 0.0002: # نفس الشرط
                        setups.append({'type': 'SELL', 'time': df.index[i-j], 'top': ot, 'bottom': ob,
                                       'entry': ob, 'sl': ot, 'tp1': ob-(ot-ob)*1.5, 'tp2': ob-(ot-ob)*2.5})
                    break
    return df, setups, sweeps

def ask_ai(setup, trend, news_warning):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"أنت خبير SMC. إشارة: {setup['type']} | دخول: {setup['entry']:.4f} | SL: {setup['sl']:.4f} | TP: {setup['tp1']:.4f} | الاتجاه: {trend}. هل الصفقة قوية؟ أجب بـ نعم أو لا مع سبب مختصر."
    models = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "allam-2-7b", "openai/gpt-oss-20b"]
    for m in models:
        try:
            res = client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model=m)
            return res.choices[0].message.content
        except:
            continue
    return "تعذر الاتصال بالذكاء الاصطناعي"

def calc_lot(entry, sl, symbol):
    risk_amount = ACCOUNT_BALANCE * (RISK_PERCENT / 100)
    dist = abs(entry - sl)
    if dist == 0: return 0
    # تقييم قيمة النقطة حسب الأصل
    if 'XAU' in symbol.upper() or 'GOLD' in symbol.upper(): pip_value = 10
    elif 'BTC' in symbol.upper(): pip_value = 1
    else: pip_value = 10000
    
    lot = risk_amount / (dist * pip_value)
    # وضع حد أقصى للوت (مثلاً 0.5) لحماية الحساب
    return min(round(lot, 2), 0.5)

def send_tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        return True
    except:
        return False

# ==========================================
# واجهة المستخدم
# ==========================================
st.title("🤖 AI SMC Trader")

with st.sidebar:
    st.header("⚙️ إعدادات")
    symbol = st.selectbox("الأصل", ["BTC-USD", "GC=F", "EURUSD=X", "ETH-USD", "GBPUSD=X", "^GSPC"])
    timeframe = st.selectbox("الفريم", ["5m", "15m", "30m", "1h", "4h", "1d"])
    analyze_btn = st.button("🚀 تحليل الآن", use_container_width=True)

if analyze_btn or 'analyzed' not in st.session_state:
    st.session_state.analyzed = True
    with st.spinner(f"🔄 جاري جلب بيانات {symbol}..."):
        try:
            df_ltf = fetch_data(symbol, timeframe)
            df_htf = fetch_data(symbol, '4h')
            
            if df_ltf.empty or df_htf.empty:
                st.error("❌ فشل جلب البيانات. تأكد من الرمز أو جرب أصلاً آخر.")
                st.stop()
            
            ema = df_htf['close'].ewm(span=50).mean().iloc[-1]
            trend = "صعودي 📈" if df_htf['close'].iloc[-1] > ema else "هبوطي 📉"
            df, setups, sweeps = analyze_smc(df_ltf)
            
            # عرض الشارت
            fig = go.Figure(data=[go.Candlestick(x=df.index, open=df['open'], high=df['high'],
                                                 low=df['low'], close=df['close'], name='Price')])
            for s in setups:
                color = 'rgba(0,255,0,0.2)' if s['type'] == 'BUY' else 'rgba(255,0,0,0.2)'
                fig.add_shape(type="rect", x0=s['time'], y0=s['bottom'], x1=df.index[-1], y1=s['top'],
                              fillcolor=color, line=dict(width=0), layer="below")
                fig.add_hline(y=s['tp1'], line_dash="dot", line_color="green", annotation_text="TP1")
                fig.add_hline(y=s['sl'], line_dash="dot", line_color="red", annotation_text="SL")
            
            fig.update_layout(xaxis_rangeslider_visible=False, height=500, template="plotly_dark", title=f"{symbol} - {timeframe}")
            st.plotly_chart(fig, use_container_width=True)
            
            col1, col2 = st.columns(2)
            col1.metric("الاتجاه العام (4H)", trend)
            col2.metric("عدد الإشارات", len(setups))
            
            if setups:
                # عرض آخر 5 إشارات فقط
                for s in setups[-5:]:
                    st.divider()
                    st.subheader(f"🎯 إشارة: {s['type']}")
                    col1, col2, col3 = st.columns(3)
                    col1.metric("💰 الدخول", f"{s['entry']:.4f}")
                    col2.metric("🛑 SL", f"{s['sl']:.4f}")
                    col3.metric("📊 اللوت", calc_lot(s['entry'], s['sl'], symbol))
            else:
                st.warning("ℹ️ لا توجد إشارات حالياً. جرب فريماً آخر.")
                
        except Exception as e:
            st.error(f"❌ خطأ: {str(e)}")

st.divider()
st.caption("⚠️ إخلاء مسؤولية: التداول يحمل مخاطر.")
