import streamlit as st
import yfinance as yf
import ccxt
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
from groq import Groq

st.set_page_config(page_title="AI SMC Trader", page_icon="📊", layout="wide")

# 🛑 ضع مفاتيحك هنا 🛑
TELEGRAM_TOKEN = "8959270070:AAGc1IxMWlc32bzBHoMX_7KcBRiJSsL0nxA"
TELEGRAM_CHAT_ID = "8619074139"
GROQ_API_KEY = "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"

ACCOUNT_BALANCE = 1000
RISK_PERCENT = 1.0

# ==========================================
# دوال جلب البيانات
# ==========================================
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
            st.success(f"✅ تم جلب بيانات {symbol} من Binance.")
        except Exception as e:
            st.warning("⚠️ فشل جلب الكريبتو من Binance، سنجرب Yahoo...")
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
            st.success(f"✅ تم جلب بيانات {symbol} من Yahoo.")
        except Exception as e:
            st.error(f"❌ فشل جلب البيانات: {e}")
            return pd.DataFrame()
            
    df.dropna(inplace=True)
    return df

# ==========================================
# محرك تحليل SMC
# ==========================================
def analyze_smc(df, lookback=10):
    df = df.copy()
    df['swing_high'] = df['high'].rolling(window=lookback*2+1, center=True).max() == df['high']
    df['swing_low'] = df['low'].rolling(window=lookback*2+1, center=True).min() == df['low']
    
    setups = []
    last_sh, last_sl = np.nan, np.nan

    for i in range(lookback, len(df) - lookback):
        if df['swing_high'].iloc[i]: last_sh = df['high'].iloc[i]
        if df['swing_low'].iloc[i]: last_sl = df['low'].iloc[i]
        
        # كشف BUY (Zone d'Achat)
        if not np.isnan(last_sh) and df['close'].iloc[i] > last_sh and df['close'].iloc[i-1] <= last_sh:
            for j in range(1, 20):
                if i-j >= 0 and df['close'].iloc[i-j] < df['open'].iloc[i-j]:
                    ot, ob = df['high'].iloc[i-j], df['low'].iloc[i-j]
                    if abs(ot - ob) > 0.0001 * ot: # فلتر المسافة
                        setups.append({'type': 'BUY', 'time': df.index[i-j], 'top': ot, 'bottom': ob,
                                       'entry': ot, 'sl': ob, 'tp1': ot+(ot-ob)*1.5, 'tp2': ot+(ot-ob)*2.5})
                    break
        
        # كشف SELL (Zone de Vente)
        if not np.isnan(last_sl) and df['close'].iloc[i] < last_sl and df['close'].iloc[i-1] >= last_sl:
            for j in range(1, 20):
                if i-j >= 0 and df['close'].iloc[i-j] > df['open'].iloc[i-j]:
                    ot, ob = df['high'].iloc[i-j], df['low'].iloc[i-j]
                    if abs(ot - ob) > 0.0001 * ot: # فلتر المسافة
                        setups.append({'type': 'SELL', 'time': df.index[i-j], 'top': ot, 'bottom': ob,
                                       'entry': ob, 'sl': ot, 'tp1': ob-(ot-ob)*1.5, 'tp2': ob-(ot-ob)*2.5})
                    break
    return df, setups

def calc_lot(entry, sl, symbol):
    risk_amount = ACCOUNT_BALANCE * (RISK_PERCENT / 100)
    distance = abs(entry - sl)
    if distance == 0: return 0
    if 'XAU' in symbol.upper() or 'GOLD' in symbol.upper(): point_value = 100
    elif 'BTC' in symbol.upper() or 'ETH' in symbol.upper(): point_value = 1
    else: point_value = 100000
    lot = risk_amount / (distance * point_value)
    # حدود قصوى آمنة
    if 'BTC' in symbol.upper() or 'ETH' in symbol.upper(): max_lot = 0.01
    elif 'XAU' in symbol.upper() or 'GOLD' in symbol.upper(): max_lot = 0.1
    else: max_lot = 0.1
    return min(round(lot, 2), max_lot)

def ask_ai(setup, trend, news_warning):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"أنت خبير SMC. إشارة: {setup['type']} | دخول: {setup['entry']:.4f} | SL: {setup['sl']:.4f} | TP: {setup['tp1']:.4f} | الاتجاه: {trend}. هل الصفقة قوية؟ أجب بـ نعم أو لا مع سبب مختصر."
    models = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "allam-2-7b", "openai/gpt-oss-20b"]
    for m in models:
        try:
            res = client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model=m)
            return res.choices[0].message.content
        except: continue
    return "تعذر الاتصال بالذكاء الاصطناعي"

def send_tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        return True
    except: return False

# ==========================================
# واجهة المستخدم
# ==========================================
st.title("📊 AI SMC Trader - Zone d'Achat & Zone de Vente")

with st.sidebar:
    st.header("⚙️ اختيار السوق")
    symbol = st.selectbox("اختر الأصل", ["BTC-USD", "ETH-USD", "GC=F (Or)", "EURUSD=X", "GBPUSD=X", "^GSPC (S&P500)"])
    # تنظيف الرمز المختار
    if " (" in symbol: symbol = symbol.split(" ")[0]
    
    timeframe = st.selectbox("الفريم الزمني", ["5m", "15m", "30m", "1h", "4h", "1d"])
    st.divider()
    analyze_btn = st.button("🚀 تحليل السوق", use_container_width=True)
    st.caption(f"💰 رأس المال: ${ACCOUNT_BALANCE} | المخاطرة: {RISK_PERCENT}%")

if analyze_btn:
    with st.spinner(f"🔄 جاري جلب بيانات {symbol} وتحليلها..."):
        try:
            df_ltf = fetch_data(symbol, timeframe)
            df_htf = fetch_data(symbol, '4h')
            
            if df_ltf.empty or df_htf.empty:
                st.error("❌ فشل جلب البيانات. جرب أصلاً آخر.")
                st.stop()
            
            ema = df_htf['close'].ewm(span=50).mean().iloc[-1]
            trend = "صعودي 📈" if df_htf['close'].iloc[-1] > ema else "هبوطي 📉"
            df, setups = analyze_smc(df_ltf)
            
            # ==========================================
            # عرض الشارت
            # ==========================================
            fig = go.Figure(data=[go.Candlestick(x=df.index, open=df['open'], high=df['high'],
                                                 low=df['low'], close=df['close'], name='Price')])
            
            if setups:
                # عرض آخر إشارة فقط للحفاظ على نظافة الشارت
                s = setups[-1]
                if s['type'] == 'BUY':
                    color = 'rgba(0, 255, 0, 0.2)' # Zone d'Achat (أخضر)
                    line_color = 'green'
                    label_zone = "Zone d'Achat (BUY)"
                else:
                    color = 'rgba(255, 0, 0, 0.2)' # Zone de Vente (أحمر)
                    line_color = 'red'
                    label_zone = "Zone de Vente (SELL)"

                # رسم مربع المنطقة (Order Block)
                fig.add_shape(type="rect", x0=s['time'], y0=s['bottom'], x1=df.index[-1], y1=s['top'],
                              fillcolor=color, line=dict(color=line_color, width=2), layer="below")
                
                # إضافة نص للمنطقة
                fig.add_annotation(x=s['time'], y=s['top'], text=label_zone, showarrow=False, 
                                   yshift=10, font=dict(color=line_color, size=12))

                # رسم خطوط الدخول، وقف الخسارة، والأهداف
                fig.add_hline(y=s['entry'], line_dash="solid", line_color="blue", annotation_text="Entry (Dخول)")
                fig.add_hline(y=s['sl'], line_dash="dash", line_color="red", annotation_text="SL (Stop Loss)")
                fig.add_hline(y=s['tp1'], line_dash="dot", line_color="green", annotation_text="TP1")
                fig.add_hline(y=s['tp2'], line_dash="dot", line_color="darkgreen", annotation_text="TP2")

            fig.update_layout(xaxis_rangeslider_visible=False, height=500, template="plotly_dark",
                              title=f"Analyse SMC: {symbol} - {timeframe}",
                              xaxis_title="Date", yaxis_title="Prix")
            st.plotly_chart(fig, use_container_width=True)
            
            # ==========================================
            # عرض المعلومات والصفقات
            # ==========================================
            if setups:
                last_setup = setups[-1]
                st.divider()
                st.subheader(f"🎯 إشارة {last_setup['type']} تم اكتشافها")
                
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("💰 الدخول", f"{last_setup['entry']:.4f}")
                col2.metric("🛑 وقف الخسارة", f"{last_setup['sl']:.4f}")
                col3.metric("🎯 TP1", f"{last_setup['tp1']:.4f}")
                col4.metric("🎯 TP2", f"{last_setup['tp2']:.4f}")
                col5.metric("📊 اللوت", calc_lot(last_setup['entry'], last_setup['sl'], symbol))
                
                if st.button("🤖 استشارة الذكاء الاصطناعي", use_container_width=True):
                    with st.spinner("جاري التحليل..."):
                        decision = ask_ai(last_setup, trend, "لا توجد أخبار")
                        st.info(f"🧠 **قرار AI:** {decision}")
                        if "نعم" in decision:
                            msg = f"🚨 إشارة {last_setup['type']} على {symbol}\nدخول: {last_setup['entry']:.4f}\nSL: {last_setup['sl']:.4f}\nTP1: {last_setup['tp1']:.4f}"
                            if send_tg(msg): st.success("✅ تم إرسال التنبيه لتلغرام!")
            else:
                st.warning("ℹ️ لا توجد إشارات Order Block حالياً في هذا الفريم. جرب فريماً آخر.")
                
        except Exception as e:
            st.error(f"❌ حدث خطأ: {str(e)}")
