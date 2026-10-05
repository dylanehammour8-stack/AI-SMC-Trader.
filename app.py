import streamlit as st
import yfinance as yf
import ccxt
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
from groq import Groq

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

if 'trades' not in st.session_state:
    st.session_state.trades = []

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
        symbol_map = {'XAUUSD': 'GC=F', 'GOLD': 'GC=F', 'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X', 'SP500': '^GSPC'}
        yf_symbol = symbol_map.get(symbol.upper(), symbol)
        period = "7d" if timeframe in ['1m', '5m', '15m', '30m'] else "1mo"
        try:
            df = yf.download(yf_symbol, interval=timeframe, period=period, progress=False)
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            df.columns = [str(c).lower() for c in df.columns]
            # إزالة المنطقة الزمنية لتجنب مشاكل العرض
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
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
    prompt = f"خبير SMC. إشارة: {setup['type']} | دخول: {setup['entry']:.4f} | SL: {setup['sl']:.4f} | TP1: {setup['tp1']:.4f} | الاتجاه: {trend}. هل الصفقة قوية؟ أجب بـ نعم أو لا."
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
# 4. مراقبة الصفقات
# ==========================================
def update_trades(current_price):
    updated_trades = []
    for trade in st.session_state.trades:
        if trade['status'] == 'Active':
            if trade['type'] == 'BUY':
                if current_price >= trade['tp2']:
                    trade['status'] = 'Closed (TP2 Hit)'; trade['pnl'] = 'Win'
                elif current_price <= trade['sl'] and trade['sl_moved'] == False:
                    trade['status'] = 'Closed (SL Hit)'; trade['pnl'] = 'Loss'
                elif current_price >= trade['tp1'] and trade['sl_moved'] == False:
                    trade['sl'] = trade['entry']
                    trade['sl_moved'] = True
                    trade['status'] = 'Active (TP1 Hit - Break Even)'
            elif trade['type'] == 'SELL':
                if current_price <= trade['tp2']:
                    trade['status'] = 'Closed (TP2 Hit)'; trade['pnl'] = 'Win'
                elif current_price >= trade['sl'] and trade['sl_moved'] == False:
                    trade['status'] = 'Closed (SL Hit)'; trade['pnl'] = 'Loss'
                elif current_price <= trade['tp1'] and trade['sl_moved'] == False:
                    trade['sl'] = trade['entry']
                    trade['sl_moved'] = True
                    trade['status'] = 'Active (TP1 Hit - Break Even)'
        updated_trades.append(trade)
    st.session_state.trades = updated_trades

# ==========================================
# 5. رسم الشارت (المناطق والصفقات داخل الشارت)
# ==========================================
def render_chart(df, zones, symbol, timeframe):
    fig = go.Figure()
    
    # 1. رسم الشموع
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'], high=df['high'],
        low=df['low'], close=df['close'],
        name='Price',
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350',
        increasing_fillcolor='#26a69a',
        decreasing_fillcolor='#ef5350'
    ))
    
    # 2. رسم المناطق وخطوط الصفقات داخل الشارت
    for z in zones:
        if z['type'] == 'BUY':
            fill_color = 'rgba(38, 166, 154, 0.15)'
            border_color = '#26a69a'
            label_text = "ZONE ACHAT"
        else:
            fill_color = 'rgba(239, 83, 80, 0.15)'
            border_color = '#ef5350'
            label_text = "ZONE VENTE"
        
        # رسم مربع المنطقة
        fig.add_shape(
            type="rect",
            x0=z['time'], y0=z['bottom'],
            x1=df.index[-1], y1=z['top'],
            fillcolor=fill_color,
            line=dict(color=border_color, width=2, dash="dot"),
            layer="below"
        )
        
        # إضافة نص اسم المنطقة
        fig.add_annotation(
            x=df.index[-1], y=z['top'],
            text=label_text,
            showarrow=False,
            xanchor='right',
            yshift=15,
            font=dict(color=border_color, size=12, family="Arial Black")
        )
        
        # رسم خطوط الصفقة (Entry, SL, TP) إذا تم لمس المنطقة
        if z['status'] == 'Touched':
            # خط الدخول
            fig.add_hline(y=z['entry'], line_dash="solid", line_color="#2962FF", line_width=2,
                          annotation_text=f"ENTRY {z['entry']:.4f}", annotation_position="right")
            # خط وقف الخسارة
            fig.add_hline(y=z['sl'], line_dash="dash", line_color="#FF1744", line_width=2,
                          annotation_text=f"SL {z['sl']:.4f}", annotation_position="right")
            # الهدف الأول
            fig.add_hline(y=z['tp1'], line_dash="dot", line_color="#00E676", line_width=2,
                          annotation_text=f"TP1 {z['tp1']:.4f}", annotation_position="right")
            # الهدف الثاني
            fig.add_hline(y=z['tp2'], line_dash="dot", line_color="#00C853", line_width=2,
                          annotation_text=f"TP2 {z['tp2']:.4f}", annotation_position="right")

    # 3. إعدادات الشارت
    fig.update_layout(
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=600,
        margin=dict(l=5, r=5, t=30, b=5),
        xaxis=dict(
            showgrid=True,
            gridcolor='rgba(42, 46, 57, 0.5)',
            title="",
            type='date',
            tickformat='%d %b %H:%M',  # تنسيق التاريخ والوقت
            nticks=6
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor='rgba(42, 46, 57, 0.5)',
            title="Prix",
            side="right",
            autorange=True
        ),
        plot_bgcolor='#131722',
        paper_bgcolor='#131722',
        hovermode='x unified',
        dragmode='pan',
        showlegend=False
    )
    
    # 4. عرض الشارت مع تفعيل التكبير باللمس
    st.plotly_chart(
        fig, 
        use_container_width=True, 
        config={
            'scrollZoom': True,          # تفعيل التكبير بالعجلة
            'displayModeBar': True,      # إظهار شريط الأدوات
            'displaylogo': False,
            'modeBarButtonsToRemove': ['lasso2d', 'select2d'],
            'doubleClick': 'reset'       # نقر مزدوج لإعادة الضبط
        }
    )

# ==========================================
# 6. واجهة المستخدم
# ==========================================
st.title("📊 AI SMC Trader")

with st.sidebar:
    st.header("⚙️ اختيار السوق")
    symbol_choice = st.selectbox("اختر الأصل", 
        ["BTC-USD", "ETH-USD", "GC=F (Or)", "EURUSD=X", "GBPUSD=X", "^GSPC (S&P500)"])
    symbol = symbol_choice.split(" ")[0]
    timeframe = st.selectbox("الفريم الزمني", ["5m", "15m", "30m", "1h", "4h", "1d"])
    
    st.divider()
    st.caption(f"💰 رأس المال: ${ACCOUNT_BALANCE} | المخاطرة: {RISK_PERCENT}%")
    
    if st.button("🔄 تحديث البيانات", use_container_width=True):
        st.rerun()
    if st.button("🗑️ مسح سجل الصفقات", use_container_width=True):
        st.session_state.trades = []
        st.rerun()

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
    update_trades(current_price)
    
    # رسم الشارت (المناطق والصفقات بالداخل)
    render_chart(df, active_zones, symbol, timeframe)
    
    touched_zones = [z for z in active_zones if z['status'] == 'Touched']
    waiting_zones = [z for z in active_zones if z['status'] == 'Waiting']
    
    st.divider()
    
    if st.session_state.trades:
        st.subheader("📋 مراقبة الصفقات")
        for t in st.session_state.trades:
            status_color = "🟢" if "TP" in t['status'] or "Break" in t['status'] else ("🔴" if "SL" in t['status'] else "🔵")
            st.markdown(f"{status_color} **{t['type']}** | دخول: {t['entry']:.4f} | SL: {t['sl']:.4f} | {t['status']}")
    
    if touched_zones:
        st.subheader("🚨 إشارات جديدة")
        for z in touched_zones:
            already_exists = any(t['time'] == z['time'] and t['type'] == z['type'] for t in st.session_state.trades)
            if not already_exists:
                st.session_state.trades.append({
                    'type': z['type'], 'time': z['time'], 'entry': z['entry'],
                    'sl': z['sl'], 'tp1': z['tp1'], 'tp2': z['tp2'],
                    'status': 'Active', 'pnl': '', 'sl_moved': False
                })
            with st.container():
                if z['type'] == 'BUY': st.success(f"🟢 **{z['type']} Signal - Zone Achat**")
                else: st.error(f"🔴 **{z['type']} Signal - Zone Vente**")
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("💰 الدخول", f"{z['entry']:.4f}")
                col2.metric("🛑 SL", f"{z['sl']:.4f}")
                col3.metric("🎯 TP1", f"{z['tp1']:.4f}")
                col4.metric("🎯 TP2", f"{z['tp2']:.4f}")
                col5.metric("📊 اللوت", calc_lot(z['entry'], z['sl'], symbol))
    
    if waiting_zones:
        st.subheader("⏳ مناطق في الانتظار")
        for z in waiting_zones:
            if z['type'] == 'BUY':
                st.info(f"🟢 **Zone Achat** | {z['bottom']:.4f} - {z['top']:.4f}")
            else:
                st.info(f"🔴 **Zone Vente** | {z['bottom']:.4f} - {z['top']:.4f}")

except Exception as e:
    st.error(f"❌ خطأ: {str(e)}")
