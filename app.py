import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings

warnings.filterwarnings('ignore')

# ================= 1. 你的專屬持股設定 =================
MY_PORTFOLIO = {
    '2345.TW': {'cost': 2093.75, 'shares': 4},       # 智邦
    '0050.TW': {'cost': 60.69, 'shares': 1369},
    '00919.TW': {'cost': 23.65, 'shares': 4188},
    '2330.TW': {'cost': 0, 'shares': 0},
    '3044.TW': {'cost': 511, 'shares': 10}           # 健鼎
}

# ================= 2. 介面與主題設定 =================
st.set_page_config(page_title="NEIL QUANT | 核心決策引擎", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
    .stApp {
        background-color: #0E1117;
        color: #C9D1D9;
    }
    .metric-card {
        background-color: #161B22;
        border-radius: 10px;
        padding: 20px;
        border: 1px solid #30363D;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .main-title {
        font-family: 'Helvetica Neue', sans-serif;
        font-weight: 800;
        font-size: 2.5rem;
        background: -webkit-linear-gradient(#00C853, #64DD17);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0px;
    }
    .sub-title {
        color: #8B949E;
        font-size: 1rem;
        margin-bottom: 30px;
    }
    </style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-title">NEIL QUANT | 核心決策引擎</p>', unsafe_allow_html=True)
st.markdown(f'<p class="sub-title">系統最新同步時間：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>', unsafe_allow_html=True)

# ================= 3. 核心運算邏輯 (保持強大) =================
@st.cache_data(ttl=3600)
def fetch_and_analyze():
    # 改用 yf.Ticker().history() 更加穩定，避開 download 的跑版問題
    try:
        twii = yf.Ticker('^TWII').history(period='3mo')
        if twii.empty:
            is_market_safe, market_msg = True, "大盤連線異常(無資料)"
        else:
            close = twii['Close']
            ma20 = close.rolling(window=20).mean()
            is_market_safe = float(close.iloc[-1]) >= float(ma20.iloc[-1])
            market_msg = f"加權指數站穩月線 ({float(ma20.iloc[-1]):.0f})" if is_market_safe else "加權指數跌破月線，啟動防禦"
    except Exception as e:
        is_market_safe, market_msg = True, f"大盤連線異常: {e}"

    results = []
    charts_data = {} 
    
    for ticker, info in MY_PORTFOLIO.items():
        try:
            df = yf.Ticker(ticker).history(period='6mo')
            if df.empty:
                raise ValueError("查無歷史資料")
            
            # 計算指標
            df['MA20'] = df['Close'].rolling(window=20).mean()
            direction = np.sign(df['Close'].diff())
            df['OBV'] = (df['Volume'] * direction).fillna(0).cumsum()
            df['Vol_MA5'] = df['Volume'].rolling(window=5).mean()
            df['OBV_20d_Max'] = df['OBV'].rolling(window=20).max()
            df['Price_20d_Change'] = (df['Close'] - df['Close'].shift(20)) / df['Close'].shift(20)
            
            latest = df.iloc[-1]
            latest_close = float(latest['Close'])
            score, signals = 0, []
            
            bias = (latest_close - float(latest['MA20'])) / float(latest['MA20']) * 100
            if 0 < bias <= 5: score += 30; signals.append("安全乖離")
            elif bias < -10: score += 30; signals.append("超跌反彈")
            elif bias > 15: score -= 20; signals.append("過熱風險")
            
            # OBV 吃貨邏輯
            if (float(df['Price_20d_Change'].iloc[-1]) <= 0.02) and (float(df['OBV'].iloc[-1]) >= float(df['OBV_20d_Max'].iloc[-1])) and (float(df['Volume'].iloc[-1]) > float(df['Vol_MA5'].iloc[-1])):
                score += 40; signals.append("🚨 主力潛伏")
                
            if not is_market_safe:
                score = int(score * 0.6)
                signals.append("⚠️ 防禦降評")
                
            if not signals: signals.append("無動能")
            
            # 決策計算
            pl_pct = ((latest_close - info['cost']) / info['cost'] * 100) if info['cost'] > 0 else 0
            if info['shares'] > 0 and pl_pct <= -8.0:
                action, sizing = "🛑 強制停損", "清空部位"
            elif score >= 60:
                action, sizing = "🟢 建議作多", "15%~30%"
            elif 40 <= score < 60:
                action, sizing = "🔄 區間觀望", "維持現狀"
            else:
                action, sizing = "💰 準備減碼", "分批了結"
                
            results.append({
                'Ticker': ticker.replace('.TW', ''),
                'Price': round(latest_close, 2),
                'Cost': info['cost'] if info['cost'] > 0 else '-',
                'P/L(%)': round(pl_pct, 2) if info['cost'] > 0 else '-',
                'Score': score,
                'Signals': " | ".join(signals),
                'Action': action,
                'Size': sizing
            })
            charts_data[ticker.replace('.TW', '')] = df.tail(60) 
            
        except Exception as e:
            # 將錯誤攔截並顯示在畫面上，不再默默失敗
            results.append({
                'Ticker': ticker.replace('.TW', ''),
                'Price': 0,
                'Cost': info['cost'] if info['cost'] > 0 else '-',
                'P/L(%)': '-',
                'Score': 0,
                'Signals': f"錯誤: {str(e)}",
                'Action': "無法分析",
                'Size': "0%"
            })
            
    return is_market_safe, market_msg, pd.DataFrame(results), charts_data

with st.spinner('連線至交易所取得即時行情...'):
    is_market_safe, market_msg, result_df, charts_data = fetch_and_analyze()

# ================= 4. 戰情總覽儀表板 =================
st.markdown("### 🌐 總體市場環境 (Macro Environment)")
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f'<div class="metric-card"><h4>大盤濾網</h4><h2 style="color: {"#00C853" if is_market_safe else "#FF1744"}">{"🟢 安全" if is_market_safe else "🔴 警戒"}</h2><p>{market_msg}</p></div>', unsafe_allow_html=True)
with c2:
    bullish = len(result_df[result_df['Score'] >= 60]) if not result_df.empty else 0
    st.markdown(f'<div class="metric-card"><h4>系統建議作多標的</h4><h2>{bullish} 檔</h2><p>動存總分 >= 60</p></div>', unsafe_allow_html=True)
with c3:
    st.markdown(f'<div class="metric-card"><h4>演算法狀態</h4><h2 style="color: #00B0FF">正常運作</h2><p>OBV/乖離率 模組上線</p></div>', unsafe_allow_html=True)
with c4:
     st.markdown(f'<div class="metric-card"><h4>風控模組</h4><h2 style="color: #FFEA00">已啟動</h2><p>-8% 絕對停損保護</p></div>', unsafe_allow_html=True)

st.write("---")

# ================= 5. 量化決策矩陣 (表格) =================
st.markdown("### 📊 量化決策矩陣 (Quantitative Matrix)")

if not result_df.empty:
    def highlight_matrix(row):
        if row['Score'] >= 60: return ['background-color: rgba(0, 200, 83, 0.15)'] * len(row)
        if '停損' in row['Action'] or '減碼' in row['Action'] or '錯誤' in row['Action']: return ['background-color: rgba(255, 23, 68, 0.15)'] * len(row)
        return [''] * len(row)

    # 針對沒有錯誤的行進行數字格式化，有錯誤的行直接顯示
    styled_df = result_df.style.apply(highlight_matrix, axis=1)
    st.dataframe(styled_df, use_container_width=True, height=250)
else:
    st.warning("無數據")

st.write("---")

# ================= 6. 專業級機構圖表 =================
st.markdown("### 📈 動能深度解析 (Deep Dive Analysis)")
st.caption("以下圖表展示近 60 日 K 線與 OBV 籌碼動能疊加圖")

if not result_df.empty and charts_data:
    selected_ticker = st.selectbox("選擇解析標的", list(charts_data.keys()))
    
    if selected_ticker in charts_data:
        df_chart = charts_data[selected_ticker]
        
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                            vertical_spacing=0.05, row_heights=[0.7, 0.3])
        
        fig.add_trace(go.Candlestick(x=df_chart.index,
                        open=df_chart['Open'], high=df_chart['High'],
                        low=df_chart['Low'], close=df_chart['Close'],
                        name='K線'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['MA20'], 
                                 line=dict(color='orange', width=2), name='20日均線'), row=1, col=1)
        
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['OBV'], 
                                 line=dict(color='#00B0FF', width=2), name='OBV 能量潮', fill='tozeroy'), row=2, col=1)

        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            height=500,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_rangeslider_visible=False
        )
        st.plotly_chart(fig, use_container_width=True)
