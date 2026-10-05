import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
from groq import Groq
from datetime import datetime

st.set_page_config(page_title="AI SMC Trader Pro", page_icon="📊", layout="wide")

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
def fetch_data(symbol, timeframe='15m', period='60d'):
    symbol_map = {'XAUUSD': 'GC=F', 'GOLD': 'GC=F', 'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X',
                  'BTC-USD': 'BTC-USD', 'ETH-USD': 'ETH-USD', 'SP500': '^GSPC', 'NAS100': '^NDX'}
    yf_symbol = symbol_map.get(symbol.upper(), symbol)
    try:
        df = yf.download(yf_symbol, interval=timeframe, period=period, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df
    except:
        return pd.DataFrame()

# ==========================================
# 2. محرك SMC (نسخة نشطة - صفقات يومية)
# ==========================================
def find_fvg(df):
    df = df.copy()
    df['fvg_bull_top'] = np.nan; df['fvg_bull_bot'] = np.nan
    df['fvg_bear_top'] = np.nan; df['fvg_bear_bot'] = np.nan
    for i in range(2, len(df)):
        if df['low'].iloc[i] > df['high'].iloc[i-2]:
            df.loc[df.index[i], 'fvg_bull_top'] = df['low'].iloc[i]
            df.loc[df.index[i], 'fvg_bull_bot'] = df['high'].iloc[i-2]
        if df['high'].iloc[i] < df['low'].iloc[i-2]:
            df.loc[df.index[i], 'fvg_bear_top'] = df['low'].iloc[i-2]
            df.loc[df.index[i], 'fvg_bear_bot'] = df['high'].iloc[i]
    return df

def detect_zones_smc_pro(df_ltf, df_htf):
    df = df_ltf.copy()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df = find_fvg(df)

    htf_ema = df_htf['close'].ewm(span=50, adjust=False).mean()
    htf_bias = 'BULL' if df_htf['close'].iloc[-1] > htf_ema.iloc[-1] else 'BEAR'

    range_high = df['high'].rolling(100).max().iloc[-1]
    range_low = df['low'].rolling(100).min().iloc[-1]
    equilibrium = (range_high + range_low) / 2

    zones = []
    avg_body = (df['close'] - df['open']).abs().rolling(20).mean()

    for i in range(15, len(df) - 5):
        # ============ BUY Setup (مخفف) ============
        # 1. Sweep: لمسة أو كسر قاع (مرن)
        prev_low_15 = df['low'].iloc[max(0, i-15):i].min()
        sweep_low = df['low'].iloc[i] <= prev_low_15 * 1.0005  # لمسة قريبة

        # 2. Displacement: 1.1x فقط
        body_next = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1]) if i+1 < len(df) else 0
        is_displacement = body_next > avg_body.iloc[i] * 1.1

        # 3. BOS: كسر قمة بسيطة
        breaks_structure = df['close'].iloc[i+1] > df['high'].iloc[max(0, i-5):i].max() if i+1 < len(df) else False

        # 4. شمعة هابطة
        is_bearish_candle = df['close'].iloc[i] < df['open'].iloc[i]

        # الشروط الأساسية (لا HTF فلتر، لا Discount فلتر)
        if is_bearish_candle and sweep_low and is_displacement and breaks_structure:
            fvg_top = None; fvg_bot = None
            for k in range(1, 4):
                if i+k < len(df) and not pd.isna(df['fvg_bull_top'].iloc[i+k]):
                    fvg_top = float(df['fvg_bull_top'].iloc[i+k])
                    fvg_bot = float(df['fvg_bull_bot'].iloc[i+k])
                    break
            zones.append({
                'type': 'BUY', 'time': df.index[i], 'bar_idx': i,
                'ob_top': float(df['high'].iloc[i]),
                'ob_bottom': float(df['low'].iloc[i]),
                'fvg_top': fvg_top, 'fvg_bot': fvg_bot,
                'htf_bias': htf_bias,
                'is_smc_pure': True,
            })

        # ============ SELL Setup (مخفف) ============
        prev_high_15 = df['high'].iloc[max(0, i-15):i].max()
        sweep_high = df['high'].iloc[i] >= prev_high_15 * 0.9995

        body_next = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1]) if i+1 < len(df) else 0
        is_displacement = body_next > avg_body.iloc[i] * 1.1

        breaks_structure = df['close'].iloc[i+1] < df['low'].iloc[max(0, i-5):i].min() if i+1 < len(df) else False

        is_bullish_candle = df['close'].iloc[i] > df['open'].iloc[i]

        if is_bullish_candle and sweep_high and is_displacement and breaks_structure:
            fvg_top = None; fvg_bot = None
            for k in range(1, 4):
                if i+k < len(df) and not pd.isna(df['fvg_bear_top'].iloc[i+k]):
                    fvg_top = float(df['fvg_bear_top'].iloc[i+k])
                    fvg_bot = float(df['fvg_bear_bot'].iloc[i+k])
                    break
            zones.append({
                'type': 'SELL', 'time': df.index[i], 'bar_idx': i,
                'ob_top': float(df['high'].iloc[i]),
                'ob_bottom': float(df['low'].iloc[i]),
                'fvg_top': fvg_top, 'fvg_bot': fvg_bot,
                'htf_bias': htf_bias,
                'is_smc_pure': True,
            })

    # 🆕 كشف FVG مستقلة (مناطق إضافية بدون OB)
    for i in range(15, len(df) - 5):
        # FVG صعودي كبير (فوق 0.3% من السعر)
        if not pd.isna(df['fvg_bull_top'].iloc[i]):
            fvg_size = df['fvg_bull_top'].iloc[i] - df['fvg_bull_bot'].iloc[i]
            if fvg_size > df['close'].iloc[i] * 0.003:  # 0.3%
                zones.append({
                    'type': 'BUY', 'time': df.index[i], 'bar_idx': i,
                    'ob_top': float(df['fvg_bull_top'].iloc[i]),
                    'ob_bottom': float(df['fvg_bull_bot'].iloc[i]),
                    'fvg_top': float(df['fvg_bull_top'].iloc[i]),
                    'fvg_bot': float(df['fvg_bull_bot'].iloc[i]),
                    'htf_bias': htf_bias,
                    'is_smc_pure': False,
                })
        if not pd.isna(df['fvg_bear_top'].iloc[i]):
            fvg_size = df['fvg_bear_top'].iloc[i] - df['fvg_bear_bot'].iloc[i]
            if fvg_size > df['close'].iloc[i] * 0.003:
                zones.append({
                    'type': 'SELL', 'time': df.index[i], 'bar_idx': i,
                    'ob_top': float(df['fvg_bear_top'].iloc[i]),
                    'ob_bottom': float(df['fvg_bear_bot'].iloc[i]),
                    'fvg_top': float(df['fvg_bear_top'].iloc[i]),
                    'fvg_bot': float(df['fvg_bear_bot'].iloc[i]),
                    'htf_bias': htf_bias,
                    'is_smc_pure': False,
                })

    # تحديث حالة اللمس (هامش 0.2%)
    current_price = float(df['close'].iloc[-1])
    current_high = float(df['high'].iloc[-1])
    current_low = float(df['low'].iloc[-1])

    active_zones = []
    for z in zones[-40:]:
        z['status'] = 'Waiting'
        buffer = current_price * 0.002

        if z['type'] == 'BUY':
            if current_low <= z['ob_top'] + buffer and current_low >= z['ob_bottom'] - buffer:
                z['status'] = 'Touched'
                z['entry'] = z['ob_top']
                z['sl'] = z['ob_bottom'] - (z['ob_top'] - z['ob_bottom']) * 0.1
                risk = z['entry'] - z['sl']
                z['tp1'] = z['entry'] + risk * 2.0
                z['tp2'] = z['entry'] + risk * 4.0
        else:
            if current_high >= z['ob_bottom'] - buffer and current_high <= z['ob_top'] + buffer:
                z['status'] = 'Touched'
                z['entry'] = z['ob_bottom']
                z['sl'] = z['ob_top'] + (z['ob_top'] - z['ob_bottom']) * 0.1
                risk = z['sl'] - z['entry']
                z['tp1'] = z['entry'] - risk * 2.0
                z['tp2'] = z['entry'] - risk * 4.0
        active_zones.append(z)
    return df, active_zones, htf_bias

# ==========================================
# 3. AI + Risk + Telegram
# ==========================================
def calc_lot(entry, sl, symbol):
    risk_amount = ACCOUNT_BALANCE * (RISK_PERCENT / 100)
    distance = abs(entry - sl)
    if distance == 0: return 0.01
    pv = 100 if ('XAU' in symbol.upper() or 'GOLD' in symbol.upper()) else (1 if ('BTC' in symbol.upper() or 'ETH' in symbol.upper()) else 100000)
    lot = risk_amount / (distance * pv)
    max_lot = 0.01 if ('BTC' in symbol.upper() or 'ETH' in symbol.upper()) else 0.1
    return max(0.01, min(round(lot, 2), max_lot))

def ask_ai(setup, htf_bias):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""خبير SMC. النوع: {setup['type']} | دخول: {setup['entry']:.4f} | SL: {setup['sl']:.4f}
TP1: {setup['tp1']:.4f} | TP2: {setup['tp2']:.4f} | HTF: {htf_bias}
هل الصفقة قوية؟ أجب بـ "نعم" أو "لا" مع سبب مختصر."""
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
    updated = []
    for t in st.session_state.trades:
        if t['status'] == 'Active':
            if t['type'] == 'BUY':
                if current_price >= t['tp2']: t['status'] = 'Closed (TP2 ✅)'
                elif current_price <= t['sl'] and not t['sl_moved']: t['status'] = 'Closed (SL ❌)'
                elif current_price >= t['tp1'] and not t['sl_moved']:
                    t['sl'] = t['entry']; t['sl_moved'] = True; t['status'] = 'Active (TP1 ✅ - BE)'
            else:
                if current_price <= t['tp2']: t['status'] = 'Closed (TP2 ✅)'
                elif current_price >= t['sl'] and not t['sl_moved']: t['status'] = 'Closed (SL ❌)'
                elif current_price <= t['tp1'] and not t['sl_moved']:
                    t['sl'] = t['entry']; t['sl_moved'] = True; t['status'] = 'Active (TP1 ✅ - BE)'
        updated.append(t)
    st.session_state.trades = updated

# ==========================================
# 5. رسم الشارت
# ==========================================
def render_chart(df, zones, symbol, timeframe):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'],
        name='Price', increasing_line_color='#26a69a', decreasing_line_color='#ef5350',
        increasing_fillcolor='#26a69a', decreasing_fillcolor='#ef5350'
    ))
    if 'ema200' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['ema200'], mode='lines',
                                 line=dict(color='#ffeb3b', width=1.5), name='EMA 200'))

    for z in zones:
        if z['type'] == 'BUY':
            ob_fill = 'rgba(38, 166, 154, 0.25)'; ob_border = '#26a69a'
            ob_label = "🟢 OB (BUY)" if z.get('is_smc_pure') else "🟢 FVG (BUY)"
        else:
            ob_fill = 'rgba(239, 83, 80, 0.25)'; ob_border = '#ef5350'
            ob_label = "🔴 OB (SELL)" if z.get('is_smc_pure') else "🔴 FVG (SELL)"

        fig.add_shape(type="rect", x0=z['time'], y0=z['ob_bottom'], x1=df.index[-1], y1=z['ob_top'],
                      fillcolor=ob_fill, line=dict(color=ob_border, width=2, dash="solid"), layer="below")
        fig.add_annotation(x=z['time'], y=z['ob_top'], text=ob_label, showarrow=False,
                           yshift=15, font=dict(color=ob_border, size=10, family="Arial Black"),
                           bgcolor='rgba(19,23,34,0.85)', bordercolor=ob_border, borderwidth=1, borderpad=2)

        if z['status'] == 'Touched':
            fig.add_hline(y=z['entry'], line_dash="solid", line_color="#2962FF", line_width=2,
                          annotation_text="ENTRY", annotation_position="right")
            fig.add_hline(y=z['sl'], line_dash="dash", line_color="#FF1744", line_width=2,
                          annotation_text="SL", annotation_position="right")
            fig.add_hline(y=z['tp1'], line_dash="dot", line_color="#00E676", line_width=2,
                          annotation_text="TP1", annotation_position="right")
            fig.add_hline(y=z['tp2'], line_dash="dot", line_color="#00C853", line_width=2,
                          annotation_text="TP2", annotation_position="right")

    fig.update_layout(
        template="plotly_dark", xaxis_rangeslider_visible=False, height=650,
        margin=dict(l=5, r=5, t=30, b=5),
        xaxis=dict(showgrid=True, gridcolor='rgba(42,46,57,0.5)', type='date', tickformat='%d %b %H:%M', nticks=6),
        yaxis=dict(showgrid=True, gridcolor='rgba(42,46,57,0.5)', title="Prix", side="right"),
        plot_bgcolor='#131722', paper_bgcolor='#131722',
        hovermode='x unified', dragmode='pan', showlegend=True
    )
    st.plotly_chart(fig, use_container_width=True, config={
        'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False,
        'modeBarButtonsToRemove': ['lasso2d', 'select2d']
    })

# ==========================================
# 6. الواجهة
# ==========================================
st.title("📊 AI SMC Trader Pro - Active Edition")
st.caption("🔥 استراتيجية نشطة: صفقات يومية مع Order Blocks + FVG")

with st.sidebar:
    st.header("⚙️ اختيار السوق")
    symbol_choice = st.selectbox("اختر الأصل",
        ["BTC-USD", "ETH-USD", "GC=F (Or)", "EURUSD=X", "GBPUSD=X", "^GSPC (S&P500)"])
    symbol = symbol_choice.split(" ")[0]
    timeframe = st.selectbox("الفريم الزمني", ["5m", "15m", "30m", "1h", "4h"], index=1)

    st.divider()
    st.caption(f"💰 رأس المال: ${ACCOUNT_BALANCE} | المخاطرة: {RISK_PERCENT}%")

    if st.button("🔄 تحديث البيانات", use_container_width=True):
        st.rerun()
    if st.button("🗑️ مسح سجل الصفقات", use_container_width=True):
        st.session_state.trades = []
        st.rerun()

period_map = {'5m': '60d', '15m': '60d', '30m': '60d', '1h': '730d', '4h': '730d'}
period = period_map.get(timeframe, '60d')

try:
    df_ltf = fetch_data(symbol, timeframe, period)
    df_htf = fetch_data(symbol, '4h', '730d')

    if df_ltf.empty or df_htf.empty:
        st.error("❌ فشل جلب البيانات.")
        st.stop()

    htf_ema = df_htf['close'].ewm(span=50).mean().iloc[-1]
    htf_bias_display = "صعودي 📈" if df_htf['close'].iloc[-1] > htf_ema else "هبوطي 📉"

    current_price = float(df_ltf['close'].iloc[-1])
    prev_price = float(df_ltf['close'].iloc[-2]) if len(df_ltf) > 1 else current_price
    change = current_price - prev_price
    change_pct = (change / prev_price) * 100 if prev_price != 0 else 0

    st.markdown(f"### {symbol} | {timeframe}")
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    col1.metric("💵 السعر الحالي", f"{current_price:.4f}", f"{change:+.4f} ({change_pct:+.2f}%)")
    col2.metric("اتجاه 4H", htf_bias_display)
    col3.metric("آخر تحديث", pd.Timestamp.now().strftime("%H:%M:%S"))
    col4.metric("الحالة", "🟢 مباشر")

    df, active_zones, htf_bias_raw = detect_zones_smc_pro(df_ltf, df_htf)
    update_trades(current_price)
    render_chart(df, active_zones, symbol, timeframe)

    touched_zones = [z for z in active_zones if z['status'] == 'Touched']
    waiting_zones = [z for z in active_zones if z['status'] == 'Waiting']

    st.divider()

    if st.session_state.trades:
        st.subheader("📋 مراقبة الصفقات")
        for t in st.session_state.trades:
            if "TP" in t['status']: color = "🟢"
            elif "SL" in t['status']: color = "🔴"
            elif "BE" in t['status']: color = "🛡️"
            else: color = "🔵"
            st.markdown(f"{color} **{t['type']}** | دخول: {t['entry']:.4f} | SL: {t['sl']:.4f} | {t['status']}")

    if touched_zones:
        st.subheader(f"🚨 {len(touched_zones)} إشارة جديدة")
        for z in touched_zones:
            already = any(t['time'] == z['time'] and t['type'] == z['type'] for t in st.session_state.trades)
            if not already:
                st.session_state.trades.append({
                    'type': z['type'], 'time': z['time'], 'entry': z['entry'],
                    'sl': z['sl'], 'tp1': z['tp1'], 'tp2': z['tp2'],
                    'status': 'Active', 'sl_moved': False
                })

            with st.container():
                label = "ORDER BLOCK" if z.get('is_smc_pure') else "FVG ZONE"
                if z['type'] == 'BUY': st.success(f"🟢 **{label} BUY**")
                else: st.error(f"🔴 **{label} SELL**")

                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("💰 الدخول", f"{z['entry']:.4f}")
                col2.metric("🛑 SL", f"{z['sl']:.4f}")
                col3.metric("🎯 TP1", f"{z['tp1']:.4f}")
                col4.metric("🎯 TP2", f"{z['tp2']:.4f}")
                col5.metric("📊 اللوت", calc_lot(z['entry'], z['sl'], symbol))

                if st.button(f"🤖 AI", key=f"ai_{z['type']}_{z['time']}"):
                    with st.spinner("..."):
                        decision = ask_ai(z, htf_bias_display)
                        st.info(f"🧠 {decision}")
                        if "نعم" in decision:
                            msg = f"🚨 {z['type']} على {symbol} {timeframe}\nدخول: {z['entry']:.4f}\nSL: {z['sl']:.4f}\nTP1: {z['tp1']:.4f}"
                            if send_tg(msg): st.success("✅ تم الإرسال!")

    if waiting_zones:
        st.subheader(f"⏳ {len(waiting_zones)} منطقة في الانتظار")
        for z in waiting_zones[:10]:  # نعرض أول 10 فقط
            icon = "🟢" if z['type'] == 'BUY' else "🔴"
            label = "OB" if z.get('is_smc_pure') else "FVG"
            st.info(f"{icon} **{label} {z['type']}** | {z['ob_bottom']:.4f} - {z['ob_top']:.4f}")

    if not touched_zones and not waiting_zones:
        st.warning("ℹ️ لا توجد مناطق. جرب أصلاً آخر أو فريماً أصغر.")

except Exception as e:
    st.error(f"❌ خطأ: {str(e)}")
