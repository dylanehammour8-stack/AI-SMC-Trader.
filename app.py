import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
from groq import Groq
from datetime import datetime

# ==========================================
# إعدادات الصفحة
# ==========================================
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
# 2. محرك SMC الاحترافي
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
    df = find_fvg(df)

    # HTF Bias (إجباري)
    htf_ema = df_htf['close'].ewm(span=50, adjust=False).mean()
    htf_bias = 'BULL' if df_htf['close'].iloc[-1] > htf_ema.iloc[-1] else 'BEAR'

    # Premium/Discount Range
    range_high = df['high'].rolling(100).max().iloc[-1]
    range_low = df['low'].rolling(100).min().iloc[-1]
    equilibrium = (range_high + range_low) / 2

    zones = []
    avg_body = (df['close'] - df['open']).abs().rolling(20).mean()

    # آخر 150 شمعة فقط
    start_idx = max(15, len(df) - 150)

    for i in range(start_idx, len(df) - 5):
        # ============ BUY Setup ============
        prev_low_20 = df['low'].iloc[max(0, i-20):i].min()
        sweep_low = df['low'].iloc[i] < prev_low_20

        body_next = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1]) if i+1 < len(df) else 0
        is_displacement = body_next > avg_body.iloc[i] * 1.5

        breaks_structure = df['close'].iloc[i+1] > df['high'].iloc[max(0, i-10):i].max() if i+1 < len(df) else False

        has_fvg = False
        for k in range(1, 4):
            if i+k < len(df) and not pd.isna(df['fvg_bull_top'].iloc[i+k]):
                has_fvg = True
                break

        is_bearish_candle = df['close'].iloc[i] < df['open'].iloc[i]
        is_discount = df['close'].iloc[i] < equilibrium

        if (htf_bias == 'BULL' and is_bearish_candle and sweep_low and
            is_displacement and breaks_structure and has_fvg and is_discount):

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
                'is_smc_pure': True,
            })

        # ============ SELL Setup ============
        prev_high_20 = df['high'].iloc[max(0, i-20):i].max()
        sweep_high = df['high'].iloc[i] > prev_high_20

        body_next = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1]) if i+1 < len(df) else 0
        is_displacement = body_next > avg_body.iloc[i] * 1.5

        breaks_structure = df['close'].iloc[i+1] < df['low'].iloc[max(0, i-10):i].min() if i+1 < len(df) else False

        has_fvg = False
        for k in range(1, 4):
            if i+k < len(df) and not pd.isna(df['fvg_bear_top'].iloc[i+k]):
                has_fvg = True
                break

        is_bullish_candle = df['close'].iloc[i] > df['open'].iloc[i]
        is_premium = df['close'].iloc[i] > equilibrium

        if (htf_bias == 'BEAR' and is_bullish_candle and sweep_high and
            is_displacement and breaks_structure and has_fvg and is_premium):

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
                'is_smc_pure': True,
            })

    # فلترة
    current_price = float(df['close'].iloc[-1])
    current_high = float(df['high'].iloc[-1])
    current_low = float(df['low'].iloc[-1])

    filtered_zones = []
    for z in zones:
        if z['type'] == 'BUY':
            if current_low < z['ob_bottom']:
                continue
        else:
            if current_high > z['ob_top']:
                continue

        if z['bar_idx'] < len(df) - 100:
            continue

        overlap = False
        for existing in filtered_zones:
            if existing['type'] == z['type']:
                if abs(z['ob_top'] - existing['ob_top']) / current_price < 0.005:
                    overlap = True
                    break
        if not overlap:
            filtered_zones.append(z)

    final_zones = filtered_zones[-5:]

    active_zones = []
    for z in final_zones:
        z['status'] = 'Waiting'
        buffer = current_price * 0.0015

        if z['type'] == 'BUY':
            if current_low <= z['ob_top'] + buffer and current_low >= z['ob_bottom'] - buffer:
                z['status'] = 'Touched'
                z['entry'] = z['ob_top']
                z['sl'] = z['ob_bottom'] - (z['ob_top'] - z['ob_bottom']) * 0.1
                risk = z['entry'] - z['sl']
                z['tp1'] = z['entry'] + risk * 3.0
                z['tp2'] = z['entry'] + risk * 5.0
        else:
            if current_high >= z['ob_bottom'] - buffer and current_high <= z['ob_top'] + buffer:
                z['status'] = 'Touched'
                z['entry'] = z['ob_bottom']
                z['sl'] = z['ob_top'] + (z['ob_top'] - z['ob_bottom']) * 0.1
                risk = z['sl'] - z['entry']
                z['tp1'] = z['entry'] - risk * 3.0
                z['tp2'] = z['entry'] - risk * 5.0
        active_zones.append(z)

    return df, active_zones, htf_bias

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

def ask_ai(setup, htf_bias):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""أنت خبير SMC محترف.
النوع: {setup['type']} | دخول: {setup['entry']:.4f} | SL: {setup['sl']:.4f}
TP1: {setup['tp1']:.4f} | TP2: {setup['tp2']:.4f}
HTF Bias: {htf_bias}
الشروط: Liquidity Sweep ✅ Displacement ✅ FVG ✅ BOS ✅ Premium/Discount ✅
هل الصفقة قوية؟ أجب بـ "نعم" أو "لا" مع سبب مختصر جداً بالعربية."""
    for m in ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "allam-2-7b", "openai/gpt-oss-20b"]:
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
# 5. رسم الشارت النظيف
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
            ob_fill = 'rgba(38, 166, 154, 0.35)'; ob_border = '#26a69a'; label = "OB 🟢"
        else:
            ob_fill = 'rgba(239, 83, 80, 0.35)'; ob_border = '#ef5350'; label = "OB 🔴"

        end_idx = min(z['bar_idx'] + 20, len(df) - 1)
        end_time = df.index[end_idx]

        fig.add_shape(type="rect", x0=z['time'], y0=z['ob_bottom'],
                      x1=end_time, y1=z['ob_top'],
                      fillcolor=ob_fill, line=dict(color=ob_border, width=1.5),
                      layer="below")
        fig.add_annotation(x=z['time'], y=z['ob_top'], text=label, showarrow=False,
                           yshift=10, font=dict(color=ob_border, size=9),
                           bgcolor='rgba(19,23,34,0.9)', borderpad=2)

        if z['status'] == 'Touched':
            fig.add_hline(y=z['entry'], line_dash="solid", line_color="#2962FF", line_width=2,
                          annotation_text="ENTRY", annotation_position="right")
            fig.add_hline(y=z['sl'], line_dash="dash", line_color="#FF1744", line_width=1.5,
                          annotation_text="SL", annotation_position="right")
            fig.add_hline(y=z['tp1'], line_dash="dot", line_color="#00E676", line_width=1.5,
                          annotation_text="TP1", annotation_position="right")
            fig.add_hline(y=z['tp2'], line_dash="dot", line_color="#00C853", line_width=1.5,
                          annotation_text="TP2", annotation_position="right")

    fig.update_layout(
        template="plotly_dark", xaxis_rangeslider_visible=False, height=650,
        margin=dict(l=5, r=5, t=30, b=5),
        xaxis=dict(showgrid=True, gridcolor='rgba(42,46,57,0.5)', type='date',
                   tickformat='%d %b %H:%M', nticks=6),
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
st.title("📊 AI SMC Trader Pro - Professional Edition")
st.caption("✨ HTF Bias → Sweep → Displacement → FVG → OB → BOS → Premium/Discount | RR 1:3 و 1:5")

with st.sidebar:
    st.header("⚙️ اختيار السوق")
    symbol_choice = st.selectbox("اختر الأصل",
        ["BTC-USD", "ETH-USD", "GC=F (Or)", "EURUSD=X", "GBPUSD=X", "^GSPC (S&P500)"])
    symbol = symbol_choice.split(" ")[0]
    timeframe = st.selectbox("الفريم الزمني", ["5m", "15m", "30m", "1h", "4h"], index=1)

    st.divider()
    st.caption(f"💰 رأس المال: ${ACCOUNT_BALANCE} | المخاطرة: {RISK_PERCENT}%")
    st.caption("🔄 اضغط تحديث عند الحاجة")

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
    col2.metric("اتجاه 4H (HTF)", htf_bias_display)
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
            if t.get('sl_moved'):
                st.caption("🛡️ SL منقول إلى نقطة التعادل (Break-even)")

    if touched_zones:
        st.subheader(f"🚨 {len(touched_zones)} إشارة نشطة")
        for z in touched_zones:
            already = any(t['time'] == z['time'] and t['type'] == z['type'] for t in st.session_state.trades)
            if not already:
                st.session_state.trades.append({
                    'type': z['type'], 'time': z['time'], 'entry': z['entry'],
                    'sl': z['sl'], 'tp1': z['tp1'], 'tp2': z['tp2'],
                    'status': 'Active', 'sl_moved': False
                })

            with st.container():
                if z['type'] == 'BUY':
                    st.success(f"🟢 **ORDER BLOCK BUY** | Sweep ✅ Displacement ✅ FVG ✅ BOS ✅ Discount ✅")
                else:
                    st.error(f"🔴 **ORDER BLOCK SELL** | Sweep ✅ Displacement ✅ FVG ✅ BOS ✅ Premium ✅")

                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("💰 الدخول", f"{z['entry']:.4f}")
                col2.metric("🛑 SL", f"{z['sl']:.4f}")
                col3.metric("🎯 TP1", f"{z['tp1']:.4f}")
                col4.metric("🎯 TP2", f"{z['tp2']:.4f}")
                col5.metric("📊 اللوت", calc_lot(z['entry'], z['sl'], symbol))

                if st.button(f"🤖 استشارة AI", key=f"ai_{z['type']}_{z['time']}"):
                    with st.spinner("..."):
                        decision = ask_ai(z, htf_bias_display)
                        st.info(f"🧠 {decision}")
                        if "نعم" in decision:
                            msg = f"""🚨 *إشارة SMC احترافية* 🚨
الأصل: {symbol} | {timeframe}
النوع: {z['type']}
HTF Bias: {htf_bias_display}
✅ Sweep | ✅ Displacement | ✅ FVG | ✅ BOS
💰 دخول: {z['entry']:.4f}
🛑 SL: {z['sl']:.4f}
🎯 TP1: {z['tp1']:.4f}
🎯 TP2: {z['tp2']:.4f}"""
                            if send_tg(msg): st.success("✅ تم إرسال التنبيه لتلغرام!")

    if waiting_zones:
        st.subheader(f"⏳ {len(waiting_zones)} منطقة في الانتظار")
        for z in waiting_zones:
            if z['type'] == 'BUY':
                st.info(f"🟢 **OB BUY** | {z['ob_bottom']:.4f} - {z['ob_top']:.4f} | بانتظار اللمس")
            else:
                st.info(f"🔴 **OB SELL** | {z['ob_bottom']:.4f} - {z['ob_top']:.4f} | بانتظار اللمس")

    if not touched_zones and not waiting_zones:
        st.warning("ℹ️ لا توجد Order Blocks مؤهلة حالياً. جرب فريماً آخر أو انتظر التحديث.")
        st.info("💡 الاستراتيجية الاحترافية تعطي 1-5 صفقات يومياً. الجودة أهم من الكمية.")

except Exception as e:
    st.error(f"❌ خطأ: {str(e)}")
    st.exception(e)
