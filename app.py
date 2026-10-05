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
    symbol_map = {
        'XAUUSD': 'GC=F', 'GOLD': 'GC=F',
        'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X',
        'BTC-USD': 'BTC-USD', 'ETH-USD': 'ETH-USD',
        'SP500': '^GSPC', 'NAS100': '^NDX'
    }
    yf_symbol = symbol_map.get(symbol.upper(), symbol)
    try:
        df = yf.download(yf_symbol, interval=timeframe, period=period, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df
    except Exception as e:
        return pd.DataFrame()

# ==========================================
# 2. محرك SMC الاحترافي
# ==========================================
def find_fvg(df):
    """اكتشاف Fair Value Gaps (فجوات القيمة العادلة)"""
    df = df.copy()
    df['fvg_bull_top'] = np.nan; df['fvg_bull_bot'] = np.nan
    df['fvg_bear_top'] = np.nan; df['fvg_bear_bot'] = np.nan
    for i in range(2, len(df)):
        # FVG صعودي: low of current > high of 2 candles ago
        if df['low'].iloc[i] > df['high'].iloc[i-2]:
            df.loc[df.index[i], 'fvg_bull_top'] = df['low'].iloc[i]
            df.loc[df.index[i], 'fvg_bull_bot'] = df['high'].iloc[i-2]
        # FVG هبوطي: high of current < low of 2 candles ago
        if df['high'].iloc[i] < df['low'].iloc[i-2]:
            df.loc[df.index[i], 'fvg_bear_top'] = df['low'].iloc[i-2]
            df.loc[df.index[i], 'fvg_bear_bot'] = df['high'].iloc[i]
    return df

def detect_zones_smc_pro(df_ltf, df_htf):
    """كشف Order Blocks احترافية بكل شروط SMC"""
    df = df_ltf.copy()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df = find_fvg(df)

    # HTF Bias
    htf_ema = df_htf['close'].ewm(span=50, adjust=False).mean()
    htf_bias = 'BULL' if df_htf['close'].iloc[-1] > htf_ema.iloc[-1] else 'BEAR'

    # Premium/Discount
    range_high = df['high'].rolling(100).max().iloc[-1]
    range_low = df['low'].rolling(100).min().iloc[-1]
    equilibrium = (range_high + range_low) / 2

    zones = []
    avg_body = (df['close'] - df['open']).abs().rolling(20).mean()

    for i in range(20, len(df) - 10):
        # ============ BUY Setup ============
        prev_low_30 = df['low'].iloc[max(0, i-30):i].min()
        sweep_low = df['low'].iloc[i] < prev_low_30 and df['close'].iloc[i] > prev_low_30

        body_next = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1])
        is_displacement = body_next > avg_body.iloc[i] * 1.8
        breaks_structure = df['close'].iloc[i+1] > df['high'].iloc[max(0, i-10):i].max()
        has_fvg = (not pd.isna(df['fvg_bull_top'].iloc[i+1])) or (not pd.isna(df['fvg_bull_top'].iloc[i+2]))
        is_bearish_candle = df['close'].iloc[i] < df['open'].iloc[i]
        is_discount = df['close'].iloc[i] < equilibrium

        if is_bearish_candle and sweep_low and is_displacement and breaks_structure and has_fvg and is_discount:
            fvg_top = None; fvg_bot = None
            if not pd.isna(df['fvg_bull_top'].iloc[i+1]):
                fvg_top = float(df['fvg_bull_top'].iloc[i+1])
                fvg_bot = float(df['fvg_bull_bot'].iloc[i+1])
            elif not pd.isna(df['fvg_bull_top'].iloc[i+2]):
                fvg_top = float(df['fvg_bull_top'].iloc[i+2])
                fvg_bot = float(df['fvg_bull_bot'].iloc[i+2])

            zones.append({
                'type': 'BUY', 'time': df.index[i], 'bar_idx': i,
                'ob_top': float(df['high'].iloc[i]),
                'ob_bottom': float(df['low'].iloc[i]),
                'fvg_top': fvg_top, 'fvg_bot': fvg_bot,
                'htf_bias': htf_bias,
            })

        # ============ SELL Setup ============
        prev_high_30 = df['high'].iloc[max(0, i-30):i].max()
        sweep_high = df['high'].iloc[i] > prev_high_30 and df['close'].iloc[i] < prev_high_30

        body_next = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1])
        is_displacement = body_next > avg_body.iloc[i] * 1.8
        breaks_structure = df['close'].iloc[i+1] < df['low'].iloc[max(0, i-10):i].min()
        has_fvg = (not pd.isna(df['fvg_bear_top'].iloc[i+1])) or (not pd.isna(df['fvg_bear_top'].iloc[i+2]))
        is_bullish_candle = df['close'].iloc[i] > df['open'].iloc[i]
        is_premium = df['close'].iloc[i] > equilibrium

        if is_bullish_candle and sweep_high and is_displacement and breaks_structure and has_fvg and is_premium:
            fvg_top = None; fvg_bot = None
            if not pd.isna(df['fvg_bear_top'].iloc[i+1]):
                fvg_top = float(df['fvg_bear_top'].iloc[i+1])
                fvg_bot = float(df['fvg_bear_bot'].iloc[i+1])
            elif not pd.isna(df['fvg_bear_top'].iloc[i+2]):
                fvg_top = float(df['fvg_bear_top'].iloc[i+2])
                fvg_bot = float(df['fvg_bear_bot'].iloc[i+2])

            zones.append({
                'type': 'SELL', 'time': df.index[i], 'bar_idx': i,
                'ob_top': float(df['high'].iloc[i]),
                'ob_bottom': float(df['low'].iloc[i]),
                'fvg_top': fvg_top, 'fvg_bot': fvg_bot,
                'htf_bias': htf_bias,
            })

    # تحديث حالة اللمس
    current_price = float(df['close'].iloc[-1])
    current_high = float(df['high'].iloc[-1])
    current_low = float(df['low'].iloc[-1])

    active_zones = []
    for z in zones[-10:]:
        z['status'] = 'Waiting'

        # فلتر HTF
        if (z['type'] == 'BUY' and htf_bias != 'BULL') or (z['type'] == 'SELL' and htf_bias != 'BEAR'):
            continue

        if z['type'] == 'BUY':
            if current_low <= z['ob_top'] and current_low >= z['ob_bottom']:
                z['status'] = 'Touched'
                z['entry'] = z['ob_top']
                z['sl'] = z['ob_bottom'] - (z['ob_top'] - z['ob_bottom']) * 0.1
                risk = z['entry'] - z['sl']
                z['tp1'] = z['entry'] + risk * 2.0
                z['tp2'] = z['entry'] + risk * 4.0
        else:
            if current_high >= z['ob_bottom'] and current_high <= z['ob_top']:
                z['status'] = 'Touched'
                z['entry'] = z['ob_bottom']
                z['sl'] = z['ob_top'] + (z['ob_top'] - z['ob_bottom']) * 0.1
                risk = z['sl'] - z['entry']
                z['tp1'] = z['entry'] - risk * 2.0
                z['tp2'] = z['entry'] - risk * 4.0

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
    prompt = f"""أنت خبير SMC محترف. لديك إشارة:
النوع: {setup['type']}
الدخول: {setup['entry']:.4f} | SL: {setup['sl']:.4f}
TP1: {setup['tp1']:.4f} | TP2: {setup['tp2']:.4f}
HTF Bias: {htf_bias}
الشروط: Liquidity Sweep ✅ Displacement ✅ FVG ✅ BOS ✅
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
                    t['sl'] = t['entry']; t['sl_moved'] = True; t['status'] = 'Active (TP1 ✅ - SL at BE)'
            else:
                if current_price <= t['tp2']: t['status'] = 'Closed (TP2 ✅)'
                elif current_price >= t['sl'] and not t['sl_moved']: t['status'] = 'Closed (SL ❌)'
                elif current_price <= t['tp1'] and not t['sl_moved']:
                    t['sl'] = t['entry']; t['sl_moved'] = True; t['status'] = 'Active (TP1 ✅ - SL at BE)'
        updated.append(t)
    st.session_state.trades = updated

# ==========================================
# 5. رسم الشارت (Order Block + FVG واضحان)
# ==========================================
def render_chart(df, zones, symbol, timeframe):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'],
        name='Price', increasing_line_color='#26a69a', decreasing_line_color='#ef5350',
        increasing_fillcolor='#26a69a', decreasing_fillcolor='#ef5350'
    ))

    # EMA 200
    if 'ema200' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['ema200'], mode='lines',
                                 line=dict(color='#ffeb3b', width=1.5), name='EMA 200'))

    # رسم Order Blocks + FVG
    for z in zones:
        if z['type'] == 'BUY':
            ob_fill = 'rgba(38, 166, 154, 0.25)'
            ob_border = '#26a69a'
            ob_label = "🟢 ORDER BLOCK (BUY)"
            fvg_fill = 'rgba(38, 166, 154, 0.10)'
        else:
            ob_fill = 'rgba(239, 83, 80, 0.25)'
            ob_border = '#ef5350'
            ob_label = "🔴 ORDER BLOCK (SELL)"
            fvg_fill = 'rgba(239, 83, 80, 0.10)'

        # 1. رسم الـ Order Block
        fig.add_shape(type="rect", x0=z['time'], y0=z['ob_bottom'],
                      x1=df.index[-1], y1=z['ob_top'],
                      fillcolor=ob_fill, line=dict(color=ob_border, width=2, dash="solid"),
                      layer="below")

        # 2. تسمية Order Block
        fig.add_annotation(x=z['time'], y=z['ob_top'], text=ob_label, showarrow=False,
                           yshift=15, font=dict(color=ob_border, size=11, family="Arial Black"),
                           bgcolor='rgba(19,23,34,0.85)', bordercolor=ob_border, borderwidth=1, borderpad=3)

        # 3. رسم FVG (منطقة التقاء)
        if z['fvg_top'] is not None and z['fvg_bot'] is not None:
            fig.add_shape(type="rect", x0=z['time'], y0=z['fvg_bot'],
                          x1=df.index[-1], y1=z['fvg_top'],
                          fillcolor=fvg_fill, line=dict(color=ob_border, width=1, dash="dot"),
                          layer="below")
            fig.add_annotation(x=df.index[-1], y=z['fvg_top'], text="FVG", showarrow=False,
                               xanchor='left', xshift=5, font=dict(color=ob_border, size=9))

        # 4. خطوط الصفقة عند اللمس
        if z['status'] == 'Touched':
            fig.add_hline(y=z['entry'], line_dash="solid", line_color="#2962FF", line_width=2,
                          annotation_text=f"ENTRY {z['entry']:.4f}", annotation_position="right")
            fig.add_hline(y=z['sl'], line_dash="dash", line_color="#FF1744", line_width=2,
                          annotation_text=f"SL {z['sl']:.4f}", annotation_position="right")
            fig.add_hline(y=z['tp1'], line_dash="dot", line_color="#00E676", line_width=2,
                          annotation_text=f"TP1 {z['tp1']:.4f}", annotation_position="right")
            fig.add_hline(y=z['tp2'], line_dash="dot", line_color="#00C853", line_width=2,
                          annotation_text=f"TP2 {z['tp2']:.4f}", annotation_position="right")

    fig.update_layout(
        template="plotly_dark", xaxis_rangeslider_visible=False, height=650,
        margin=dict(l=5, r=5, t=30, b=5),
        xaxis=dict(showgrid=True, gridcolor='rgba(42,46,57,0.5)', type='date',
                   tickformat='%d %b %H:%M', nticks=6),
        yaxis=dict(showgrid=True, gridcolor='rgba(42,46,57,0.5)', title="Prix", side="right"),
        plot_bgcolor='#131722', paper_bgcolor='#131722',
        hovermode='x unified', dragmode='pan', showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True, config={
        'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False,
        'modeBarButtonsToRemove': ['lasso2d', 'select2d']
    })

# ==========================================
# 6. الواجهة الرئيسية
# ==========================================
st.title("📊 AI SMC Trader Pro")
st.caption("✨ استراتيجية SMC كاملة: HTF Bias → Liquidity Sweep → Displacement → FVG → Order Block → BOS")

with st.sidebar:
    st.header("⚙️ اختيار السوق")
    symbol_choice = st.selectbox("اختر الأصل",
        ["BTC-USD", "ETH-USD", "GC=F (Or)", "EURUSD=X", "GBPUSD=X", "^GSPC (S&P500)"])
    symbol = symbol_choice.split(" ")[0]
    timeframe = st.selectbox("الفريم الزمني", ["5m", "15m", "30m", "1h", "4h"])

    st.divider()
    st.caption(f"💰 رأس المال: ${ACCOUNT_BALANCE} | المخاطرة: {RISK_PERCENT}%")

    if st.button("🔄 تحديث البيانات", use_container_width=True):
        st.rerun()
    if st.button("🗑️ مسح سجل الصفقات", use_container_width=True):
        st.session_state.trades = []
        st.rerun()

# تحديد الفترة حسب الفريم (قيود ياهو)
period_map = {'5m': '60d', '15m': '60d', '30m': '60d', '1h': '730d', '4h': '730d'}
period = period_map.get(timeframe, '60d')

try:
    df_ltf = fetch_data(symbol, timeframe, period)
    df_htf = fetch_data(symbol, '4h', '730d')

    if df_ltf.empty or df_htf.empty:
        st.error("❌ فشل جلب البيانات. جرب أصلاً آخر.")
        st.stop()

    # HTF Trend
    htf_ema = df_htf['close'].ewm(span=50).mean().iloc[-1]
    htf_bias_display = "صعودي 📈" if df_htf['close'].iloc[-1] > htf_ema else "هبوطي 📉"

    # السعر الحالي
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

    # تحليل SMC
    df, active_zones, htf_bias_raw = detect_zones_smc_pro(df_ltf, df_htf)
    update_trades(current_price)

    # رسم الشارت
    render_chart(df, active_zones, symbol, timeframe)

    touched_zones = [z for z in active_zones if z['status'] == 'Touched']
    waiting_zones = [z for z in active_zones if z['status'] == 'Waiting']

    st.divider()

    # مراقبة الصفقات
    if st.session_state.trades:
        st.subheader("📋 مراقبة الصفقات المفتوحة")
        for t in st.session_state.trades:
            if "TP" in t['status']: color = "🟢"
            elif "SL" in t['status']: color = "🔴"
            elif "BE" in t['status']: color = "🛡️"
            else: color = "🔵"
            st.markdown(f"{color} **{t['type']}** | دخول: {t['entry']:.4f} | SL: {t['sl']:.4f} | {t['status']}")
            if t.get('sl_moved'):
                st.caption("🛡️ تم نقل وقف الخسارة إلى نقطة التعادل (Break-even)")

    # الإشارات الجديدة
    if touched_zones:
        st.subheader("🚨 إشارات SMC احترافية (كل الشروط محققة)")
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

    # المناطق المنتظرة
    if waiting_zones:
        st.subheader("⏳ مناطق Order Block في الانتظار (لم تُلمس)")
        for z in waiting_zones:
            if z['type'] == 'BUY':
                st.info(f"🟢 **ORDER BLOCK BUY** | النطاق: {z['ob_bottom']:.4f} - {z['ob_top']:.4f} | السعر ينتظر الوصول")
            else:
                st.info(f"🔴 **ORDER BLOCK SELL** | النطاق: {z['ob_bottom']:.4f} - {z['ob_top']:.4f} | السعر ينتظر الوصول")

    if not touched_zones and not waiting_zones:
        st.warning("ℹ️ لا توجد Order Blocks مؤهلة حالياً (لم تتحقق كل الشروط). جرب فريماً آخر أو انتظر التحديث.")
        st.info("💡 نصيحة: استراتيجية SMC الاحترافية تعطي 20-50 صفقة شهرياً فقط. الجودة أهم من الكمية.")

except Exception as e:
    st.error(f"❌ خطأ: {str(e)}")
    st.exception(e)
