import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings

warnings.filterwarnings('ignore')

# ================= 1. 專屬持股設定 =================
MY_PORTFOLIO = {
    '2345.TW': {'cost': 2093.75, 'shares': 4, 'name': '智邦'},
    '0050.TW': {'cost': 60.69, 'shares': 1369, 'name': '元大台灣50'},
    '00919.TW': {'cost': 23.65, 'shares': 4188, 'name': '群益台灣精選高息'},
    '2330.TW': {'cost': 0, 'shares': 0, 'name': '台積電'},
    '3044.TW': {'cost': 511, 'shares': 10, 'name': '健鼎'}
}

# ================= 2. 介面與主題設定 =================
st.set_page_config(page_title="NEIL QUANT | 核心決策引擎", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #C9D1D9; }
    
    /* 頂部宏觀卡片 */
    .macro-card {
        background-color: #161B22; border-radius: 12px; padding: 20px;
        border: 1px solid #30363D; box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        height: 140px; display: flex; flex-direction: column; justify-content: space-between;
    }
    .macro-card h4 { color: #8B949E; margin: 0; font-size: 0.9rem; font-weight: 500;}
    .macro-card h2 { margin: 0; font-size: 1.8rem; font-weight: 700; padding-top: 5px;}
    .macro-card p { color: #8B949E; margin: 0; font-size: 0.8rem; }
    
    /* 個股決策卡片 (取代原本的表格) */
    .stock-card {
        background-color: #1C2128; border-radius: 12px; padding: 15px;
        border-left: 5px solid #444C56; margin-bottom: 15px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2); transition: transform 0.2s;
    }
    .stock-card:hover { transform: translateY(-2px); }
    .stock-card.bullish { border-left-color: #2EA043; background-color: rgba(46, 160, 67, 0.05); } /* 綠燈卡片 */
    .stock-card.bearish { border-left-color: #F85149; } /* 紅燈卡片 */
    
    .stock-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #30363D; padding-bottom: 10px; margin-bottom: 10px; }
    .stock-title { font-size: 1.2rem; font-weight: 700; color: #E6EDF3; }
    .stock-price { font-size: 1.2rem; font-weight: 700; font-family: monospace; }
    
    .stock-body { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
    .data-label { font-size: 0.75rem; color: #8B949E; text-transform: uppercase; }
    .data-value { font-size: 1rem; font-weight: 600; color: #E6EDF3; font-family: monospace; }
    .profit-positive { color: #2EA043; }
    .profit-negative { color: #F85149; }
    
    .stock-footer { margin-top: 10px; padding-top: 10px; border-top: 1px dashed #30363D; display: flex; justify-content: space-between; }
    .signal-badge { background-color: #21262D; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; color: #58A6FF; border: 1px solid #30363D; }
    .action-badge { font-weight: 700; font-size: 0.9rem; }
    
    .main-title { font-family: 'Helvetica Neue', sans-serif; font-weight: 800; font-size: 2.2rem; background: -webkit-linear-gradient(#58A6FF, #1F6FEB); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 0px; }
    .sub-title { color: #8B949E; font-size: 0.9rem; margin-bottom: 20px; }
    </style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-title">NEIL QUANT | 核心決策引擎 (機構版)</p>', unsafe_allow_html=True)
st.markdown(f'<p class="sub-title">系統最新同步時間：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>', unsafe_allow_html=True)

# ================= 3. 核心運算邏輯 =================
@st.cache_data(ttl=3600)
def fetch_and_analyze():
    try:
        twii = yf.Ticker('^TWII').history(period='3mo')
        if twii.empty:
            is_market_safe, market_msg, market_bias = True, "連線異常", 0
        else:
            close = twii['Close']
            ma20 = close.rolling(window=20).mean()
            latest_close = float(close.iloc[-1])
            latest_ma20 = float(ma20.iloc[-1])
            is_market_safe = latest_close >= latest_ma20
            market_bias = ((latest_close - latest_ma20) / latest_ma20) * 100
            
            if is_market_safe:
                market_msg = f"大盤距月線乖離 +{market_bias:.1f}%，水位健康"
            else:
                market_msg = f"大盤距月線乖離 {market_bias:.1f}%，資金退潮中"
    except Exception as e:
        is_market_safe, market_msg, market_bias = True, f"錯誤: {e}", 0

    results = []
    charts_data = {} 
    
    for ticker, info in MY_PORTFOLIO.items():
        try:
            df = yf.Ticker(ticker).history(period='6mo')
            if df.empty: continue
            
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
            
            if bias < -15: score += 50; signals.append("極度超跌")
            elif bias < -5: score += 30; signals.append("回檔修正")
            elif 0 < bias <= 5: score += 20; signals.append("合理區間")
            elif bias > 15: score -= 20; signals.append("乖離過大")
            
            if (float(df['Price_20d_Change'].iloc[-1]) <= 0.02) and (float(df['OBV'].iloc[-1]) >= float(df['OBV_20d_Max'].iloc[-1])) and (float(df['Volume'].iloc[-1]) > float(df['Vol_MA5'].iloc[-1])):
                score += 40; signals.append("🚨 主力吃貨")
                
            if not is_market_safe: score = int(score * 0.8) 
            if not signals: signals.append("動能平穩")
            
            pl_pct = ((latest_close - info['cost']) / info['cost'] * 100) if info['cost'] > 0 else 0
            
            if score >= 60:
                action, sizing, status_class = "🟢 積極加碼", "分批建倉", "bullish"
            elif 40 <= score < 60:
                action, sizing, status_class = ("🔄 零股定額" if bias < 0 else "☕ 抱緊處理"), ("逢低買進" if bias < 0 else "維持部位"), ""
            else:
                action, sizing, status_class = ("💰 停利入袋" if (info['shares'] > 0 and pl_pct > 20) else "☕ 觀望不急"), ("收回本金" if (info['shares'] > 0 and pl_pct > 20) else "暫不投入"), ("bearish" if pl_pct < -10 else "")
                
            results.append({
                'Ticker': ticker.replace('.TW', ''),
                'Name': info['name'],
                'Price': latest_close,
                'Cost': info['cost'],
                'PL_Pct': pl_pct,
                'Score': score,
                'Signals': " | ".join(signals),
                'Action': action,
                'Size': sizing,
                'Class': status_class
            })
            charts_data[ticker.replace('.TW', '')] = df.tail(60) 
            
        except Exception: pass
            
    return is_market_safe, market_msg, market_bias, results, charts_data

with st.spinner('同步最新籌碼與報價...'):
    is_market_safe, market_msg, market_bias, results, charts_data = fetch_and_analyze()

# ================= 4. 總體市場環境 (Macro Dashboard) =================
st.markdown("### 🌐 總經與風控儀表板")
c1, c2, c3, c4 = st.columns(4)

with c1:
    m_color = "#2EA043" if is_market_safe else "#F85149"
    m_status = "大盤站穩月線" if is_market_safe else "大盤跌破月線"
    st.markdown(f'<div class="macro-card"><h4>大盤趨勢濾網</h4><h2 style="color: {m_color}">{m_status}</h2><p>{market_msg}</p></div>', unsafe_allow_html=True)

with c2:
    bullish_count = sum(1 for r in results if r['Class'] == 'bullish')
    st.markdown(f'<div class="macro-card"><h4>系統偵測買點</h4><h2>{bullish_count} 檔標的</h2><p>符合超跌或主力吃貨特徵</p></div>', unsafe_allow_html=True)

with c3:
    st.markdown(f'<div class="macro-card"><h4>目前資金策略</h4><h2 style="color: #58A6FF">{"積極佈局" if is_market_safe else "保守防禦"}</h2><p>基於大盤乖離率自動判定</p></div>', unsafe_allow_html=True)

with c4:
    total_pl = sum(r['PL_Pct'] for r in results if r['Cost'] > 0)
    avg_pl = (total_pl / sum(1 for r in results if r['Cost'] > 0)) if sum(1 for r in results if r['Cost'] > 0) > 0 else 0
    pl_color = "#2EA043" if avg_pl >= 0 else "#F85149"
    st.markdown(f'<div class="macro-card"><h4>投資組合平均帳面</h4><h2 style="color: {pl_color}">{avg_pl:+.1f}%</h2><p>含息總報酬表現</p></div>', unsafe_allow_html=True)

st.write("---")

# ================= 5. 量化決策矩陣 (精美卡片化 UI) =================
st.markdown("### 📊 量化決策矩陣")

if results:
    # 每行顯示兩檔股票的卡片，在大螢幕上更好看
    col1, col2 = st.columns(2)
    
    for idx, row in enumerate(results):
        target_col = col1 if idx % 2 == 0 else col2
        
        pl_class = "profit-positive" if row['PL_Pct'] > 0 else ("profit-negative" if row['PL_Pct'] < 0 else "")
        pl_display = f"{row['PL_Pct']:+.2f}%" if row['Cost'] > 0 else "-"
        cost_display = f"{row['Cost']:.2f}" if row['Cost'] > 0 else "未持有"
        
        card_html = f"""
        <div class="stock-card {row['Class']}">
            <div class="stock-header">
                <span class="stock-title">{row['Ticker']} {row['Name']}</span>
                <span class="stock-price">${row['Price']:.2f}</span>
            </div>
            <div class="stock-body">
                <div><div class="data-label">買進成本</div><div class="data-value">{cost_display}</div></div>
                <div><div class="data-label">帳面損益</div><div class="data-value {pl_class}">{pl_display}</div></div>
                <div><div class="data-label">動能總分</div><div class="data-value">{row['Score']} / 100</div></div>
                <div><div class="data-label">部位建議</div><div class="data-value" style="color:#8B949E">{row['Size']}</div></div>
            </div>
            <div class="stock-footer">
                <span class="signal-badge">📡 {row['Signals']}</span>
                <span class="action-badge">{row['Action']}</span>
            </div>
        </div>
        """
        target_col.markdown(card_html, unsafe_allow_html=True)
else:
    st.warning("無數據")

st.write("---")

# ================= 6. 專業級機構圖表 =================
st.markdown("### 📈 動能深度解析")

if results and charts_data:
    options = [f"{r['Ticker']} {r['Name']}" for r in results]
    selected_option = st.selectbox("選擇解析標的", options)
    selected_ticker = selected_option.split(" ")[0]
    
    if selected_ticker in charts_data:
        df_chart = charts_data[selected_ticker]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
        
        fig.add_trace(go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name='K線'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['MA20'], line=dict(color='orange', width=2), name='20日月線'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['OBV'], line=dict(color='#58A6FF', width=2), name='OBV 能量潮', fill='tozeroy'), row=2, col=1)

        fig.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', height=500, margin=dict(l=0, r=0, t=10, b=0), xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)
