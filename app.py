"""NEIL QUANT 2：私人台股研究與持股決策介面。"""
import hashlib
import hmac
import json
import os
from datetime import datetime
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from core import VERSION, TZ, analyze_portfolio, market_state, completed_bars, backtest_ma
from storage import Store, validate_config, dumps
from market_data import fetch_bundle, download_bars, fetch_news

st.set_page_config(page_title='NEIL QUANT 2｜台股決策', page_icon='📈', layout='wide')


def secret(name, default=''):
    value = os.environ.get(name)
    if value is not None:
        return value
    try:
        return st.secrets.get(name, default)
    except (FileNotFoundError, KeyError):
        return default


# 單一私人帳戶。公開部署必須設密碼；LOCAL_ONLY 僅搭配127.0.0.1使用。
password = secret('APP_PASSWORD')
if not password and secret('LOCAL_ONLY') != '1':
    st.title('NEIL QUANT 2｜初次設定')
    st.info('請在 Streamlit Secrets 設定 APP_PASSWORD，再重新啟動。Windows 本機可使用 start_local.bat。')
    st.stop()
if password:
    if not st.session_state.get('authenticated'):
        with st.form('login'):
            entered = st.text_input('私人帳本密碼', type='password')
            submitted = st.form_submit_button('登入')
        if submitted:
            if hmac.compare_digest(entered.encode(), str(password).encode()):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error('密碼不正確')
        st.stop()

st.markdown('''<style>
.stApp {background:#0c1422;color:#edf3ff}
[data-testid="stMetric"] {background:#162237;border:1px solid #263b56;border-radius:12px;padding:16px}
h1 {letter-spacing:-1px} .block-container {padding-top:2rem}
</style>''', unsafe_allow_html=True)
st.title('NEIL QUANT 2')
st.caption('台股持股決策工作台 · 完整日線 · 有條件的建議 · 人工確認交易')

@st.cache_resource
def get_store(url):
    return Store(url or None)

try:
    store = get_store(secret('DATABASE_URL'))
    config, revision = store.load()
    config = validate_config(config)
except Exception:
    st.error('帳本無法載入，請檢查資料庫連線與設定。未使用預設數值覆蓋既有帳本。')
    st.stop()

page = st.sidebar.radio('工作台', ['我的持股','今日建議','技術圖表','組合風險','回測與日誌','AI與新聞'])
st.sidebar.caption(f'規則版本 {VERSION}｜Yahoo Finance 日線')
if not store.persistent_remote:
    st.sidebar.info('目前使用本機資料庫。雲端部署請設定持久 DATABASE_URL；帳本可在「我的持股」匯出。')
if password and st.sidebar.button('登出'):
    st.session_state.clear()
    st.rerun()

LABELS = {'ticker':'代碼（含.TW/.TWO）','name':'名稱','shares':'股數','cost':'每股平均成本',
          'mode':'持股用途','max_weight':'配置上限(%)','sell_tax':'賣出稅率（小數）','confirmed':'已核對庫存'}
RESULT_LABELS = {'ticker':'代碼','price':'參考收盤','asof':'行情日期','shares':'股數','cost':'平均成本',
                 'market_value':'市值','pnl':'未實現損益','pnl_pct':'損益(%)','weight':'配置(%)',
                 'score':'規則分數','action':'條件建議','buy_shares':'候選股數','buy_budget':'預估買入金額',
                 'quality':'資料狀態','evidence':'規則依據','stop':'2ATR失效參考','target':'2R情境參考'}


def csv_bytes(df):
    return df.to_csv(index=False).encode('utf-8-sig')


if page == '我的持股':
    st.subheader('持股帳本與風險設定')
    st.info('已帶入原始 app.py 的5檔名單。原檔部分註明「舉例」，請核對後勾選。平均成本採含買進費用口徑；本版為庫存快照，不含歷史已實現損益與配息帳務。')
    st.warning('0050 的 180.5元／2000股保留原值；請依券商目前庫存核對分割後股數與成本，系統不猜測或自動修改。')
    with st.form('portfolio'):
        frame = pd.DataFrame(config['positions'], columns=LABELS.keys())
        edited = st.data_editor(frame, num_rows='dynamic', hide_index=True, use_container_width=True,
          column_config={**{k:st.column_config.Column(v) for k,v in LABELS.items()},
             'ticker':st.column_config.TextColumn(LABELS['ticker'],required=True),
             'mode':st.column_config.SelectboxColumn('持股用途',options=['長期','波段'],required=True),
             'confirmed':st.column_config.CheckboxColumn('已核對庫存',default=False),
             'shares':st.column_config.NumberColumn('股數',min_value=0,step=1),
             'cost':st.column_config.NumberColumn('每股平均成本',min_value=0.0,format='%.4f')})
        a,b,c = st.columns(3)
        s = config['settings'].copy()
        s['cash'] = a.number_input('可用現金（元）',min_value=0.0,value=float(s['cash']),step=1000.0)
        s['risk_pct'] = b.number_input('本批新增部位風險預算(%)',0.1,5.0,float(s['risk_pct']),0.1)
        s['stop_pct'] = c.number_input('波段成本停損檢視(%)',0.1,50.0,float(s['stop_pct']),0.5)
        s['min_score'] = a.number_input('候選最低分數',1,100,int(s['min_score']))
        s['fee_rate'] = b.number_input('單邊手續費率（小數）',0.0,0.1,float(s['fee_rate']),format='%.6f')
        s['min_fee'] = c.number_input('單筆最低手續費（元）',0.0,value=float(s['min_fee']))
        s['slippage'] = a.number_input('單邊滑價假設（小數）',0.0,0.1,float(s['slippage']),format='%.4f')
        st.caption('費率為可調試算假設，需依券商及商品核實。新增列請填完整欄位；純觀察填0股、成本0。')
        if st.form_submit_button('儲存帳本', type='primary'):
            try:
                new = {'positions':edited.to_dict('records'),'settings':s}
                store.save(new, revision)
                st.session_state.pop('ai_result',None)
                st.rerun()
            except (ValueError,TypeError,KeyError) as exc:
                st.error(f'未儲存：{exc}')
            except Exception:
                st.error('儲存失敗，請检查資料庫連線；既有帳本未被清空。')
    st.download_button('匯出帳本備份 JSON', dumps(config).encode(), 'portfolio_backup.json','application/json')
    uploaded = st.file_uploader('匯入本系統帳本備份',type=['json'])
    if uploaded and st.button('驗證並替換帳本'):
        try:
            store.save(json.load(uploaded), revision)
            st.rerun()
        except (ValueError,KeyError,TypeError) as exc:
            st.error(f'匯入失敗：{exc}')
        except Exception:
            st.error('匯入未完成，請檢查資料庫連線。')
    st.stop()


@st.cache_data(ttl=900, show_spinner=False)
def cached_bundle(tickers):
    histories, errors = fetch_bundle(tickers)
    return histories, errors, datetime.now(TZ).isoformat(timespec='seconds')


@st.cache_data(ttl=3600, show_spinner=False)
def adjusted_history(ticker, period):
    return download_bars(ticker,period=period,adjusted=True)


@st.cache_data(ttl=1800, show_spinner=False)
def news_for(ticker):
    return fetch_news(ticker)


if st.sidebar.button('重新取得行情'):
    cached_bundle.clear()
    adjusted_history.clear()
    news_for.clear()
with st.spinner('取得 Yahoo Finance 日線資料…'):
    histories, errors, fetched = cached_bundle(tuple(p['ticker'] for p in config['positions']))
market = market_state(histories.get('^TWII'))
rows, charts, totals = analyze_portfolio(config['positions'],config['settings'],histories,market)
st.caption(f'資料抓取時間：{fetched}；實際行情日期請看各檔。資料可能延遲，非即時委託報價。')
if errors:
    st.warning('部分行情未取得；失敗標的不會被悄悄隱藏。')
    with st.expander('查看資料問題'):
        st.json(errors)
if not rows:
    st.info('請先新增持股或觀察名單。')
    st.stop()
result = pd.DataFrame(rows)
selected = None

if page == '今日建議':
    a,b,c,d = st.columns(4)
    a.metric('帳本總資產',f"{totals['equity']:,.0f}" if totals['complete'] else '待核對')
    b.metric('未實現損益',f"{totals['pnl']:,.0f}" if totals['complete'] else '待核對')
    c.metric('可用現金',f"{totals['cash']:,.0f}")
    d.metric('候選標的',sum(r['buy_shares']>0 for r in rows))
    st.info(market['message'])
    if not totals['complete']:
        st.warning('持股未確認、報價不足或日期不一致：總資產與新增部位計算暫停。')
    fields=['ticker','asof','shares','cost','price','pnl_pct','weight','score','action','buy_shares','buy_budget']
    st.dataframe(result[fields].rename(columns=RESULT_LABELS),hide_index=True,use_container_width=True)
    st.caption('分數是規則符合程度，不是勝率。候選股數依同一批現金與風險預算配置，不是委託單；隔日需重新計價。')
    for r in rows:
        with st.expander(f"{r['ticker']} · {r['action']}"):
            st.write(r['evidence'])
            st.write('資料狀態：'+r['quality'])
            if r['mode']=='波段' and r['stop'] is not None:
                st.write(f"2ATR失效參考：{r['stop']:.2f}；2R上行情境：{r['target']:.2f}。未校正委託跳動單位，非預測目標；跳空仍可超過風險預算。")
    if st.button('保存今天的決策快照'):
        n = store.record(rows,config,market)
        st.success(f'新增 {n} 筆。相同日期／代碼／版本不重複，也不覆寫第一次快照。')
    st.download_button('下載本次報告 CSV',csv_bytes(result.rename(columns=RESULT_LABELS)),'daily_report.csv','text/csv')

elif page == '技術圖表':
    if not charts:
        st.info('目前沒有可繪製的行情。')
        st.stop()
    ticker = st.selectbox('標的',list(charts))
    frequency = st.radio('週期',['日線','週線'],horizontal=True)
    raw = charts[ticker]
    chart = raw.tail(180)
    if frequency=='週線':
        chart = raw.resample('W-FRI').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
        # 週五尚未收盤或當週資料未齊，捨棄標記日在最新日線之後的週。
        chart = chart[chart.index <= raw.index[-1]].copy()
        chart['MA20'] = chart.Close.rolling(20).mean()
        chart['MA60'] = chart.Close.rolling(60).mean()
        st.caption('週線僅採週五標記已到的週；假日週保守排除。均線為20週／60週。')
    fig = make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[0.75,0.25],vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(x=chart.index,open=chart.Open,high=chart.High,low=chart.Low,close=chart.Close,name='價格'),row=1,col=1)
    for col,color in [('MA20','#52dfbc'),('MA60','#f0bc65')]:
        fig.add_trace(go.Scatter(x=chart.index,y=chart[col],name=col,line_color=color),row=1,col=1)
    fig.add_trace(go.Bar(x=chart.index,y=chart.Volume,name='成交量',marker_color='#486887'),row=2,col=1)
    fig.update_layout(template='plotly_dark',height=600,xaxis_rangeslider_visible=False,margin=dict(t=20,b=10))
    st.plotly_chart(fig,use_container_width=True)
    x=raw.iloc[-1]
    st.dataframe(pd.DataFrame([{'RSI14':x.RSI14,'ATR14':x.ATR14,'前20日低點':x.Support20,'前20日高點':x.Resistance20,'量比':x.VolumeRatio}]),hide_index=True)
    st.caption('價格為 Yahoo Close/OHLC 口徑，歷史分割可能回溯調整；指標可受除息影響。前20日高低點只是區間參考。')

elif page == '組合風險':
    if not totals['complete']:
        st.warning('請先確認持股，並取得同日有效報價，才可進行組合壓力試算。')
        st.stop()
    held = result[result.shares>0]
    st.subheader('目前配置')
    st.dataframe(held[['ticker','market_value','weight','max_weight']].rename(columns=RESULT_LABELS),hide_index=True)
    st.subheader('價格壓力試算')
    st.caption('假設所有持股同幅下跌、現金不變；這不是「大盤跌幅」預測，也不含槓桿、稅費與相關性變動。')
    stress=[]
    for shock in [0.1,0.2,0.3]:
        loss=totals['known_value']*shock
        stress.append({'持股價格下跌':f'{shock:.0%}','估計損失':loss,'剩餘資產':totals['equity']-loss,
                       '總資產減少(%)':100*loss/totals['equity'] if totals['equity'] else 0})
    st.dataframe(pd.DataFrame(stress),hide_index=True,use_container_width=True)
    st.info('ETF 成分穿透、產業／地區曝險、利率與衰退模型尚未接入；不同ETF不代表已充分分散。')

elif page == '回測與日誌':
    st.subheader('MA20／MA60 調整價研究回測')
    st.caption('此頁測試均線策略，不是今日多因子分數策略。前一日MA20>MA60，下一交易日開盤持有；反向則退出。')
    ticker=st.selectbox('回測標的',[r['ticker'] for r in rows])
    period=st.selectbox('資料期間',['2y','5y','10y'])
    st.warning('簡化研究假設：調整價、分數單位、費率／滑價；未模擬最低手續費、整股零股、漲跌停未成交。勝率不是實戰勝率。')
    if st.button('執行研究回測'):
        try:
            p=next(x for x in config['positions'] if x['ticker']==ticker)
            s=config['settings']
            bars=completed_bars(adjusted_history(ticker,period))
            curve,trades,stats=backtest_ma(bars,s['fee_rate'],p['sell_tax'],s['slippage'])
            st.line_chart(curve)
            st.dataframe(pd.DataFrame([stats]),hide_index=True)
            st.dataframe(trades,hide_index=True)
            st.caption('獲利因子無虧損樣本時留空；期末未平倉不計入勝率。報酬為假設清算淨值，配息效果包含於調整價。')
            st.download_button('下載回測曲線',csv_bytes(curve.reset_index()),'backtest.csv')
        except Exception as exc:
            st.error(f'回測無法完成：{type(exc).__name__}。請確認行情長度與來源。')
    st.subheader('已保存的決策快照')
    records=store.journal()
    if records:
        journal=pd.DataFrame([dict(紀錄時間=x['recorded_at'],版本=x['version'],**x['row']) for x in records])
        st.dataframe(journal.rename(columns=RESULT_LABELS),hide_index=True)
        st.download_button('下載決策日誌',csv_bytes(journal.rename(columns=RESULT_LABELS)),'journal_v2.csv')
        horizon=st.number_input('訊號後持有交易日',1,60,5)
        if st.button('評估候選訊號後續報酬'):
            from evaluate_journal import evaluate_records
            evaluated=evaluate_records(records,int(horizon))
            st.dataframe(evaluated,hide_index=True)
            st.caption('第一個後續交易日開盤進入，第N個交易日收盤退出；訊號可能重疊，非独立交易、非組合報酬。')
    else:
        st.info('尚無日誌，可從今日建議保存，或執行 stock_monitor.py。')

elif page == 'AI與新聞':
    ticker=st.selectbox('研究標的',[r['ticker'] for r in rows])
    row=next(r for r in rows if r['ticker']==ticker)
    st.subheader('新聞與AI情境解讀')
    st.caption('新聞來源：Yahoo Finance 聚合。台股覆蓋可能不完整；摘要不代表全文，請核對原始報導。財報與估值資料尚未自動接入。')
    if st.button('取得新聞摘要'):
        try:
            st.session_state['news_'+ticker]=news_for(ticker)
        except Exception:
            st.warning('新聞暫時無法取得；可貼上有來源的研究素材。')
    news=list(st.session_state.get('news_'+ticker,[]))
    for n in news:
        st.write(f"{n['id']} · {n['title']} · {n['published_at']}")
        st.link_button('原文',n['url'])
    with st.expander('補充公告或新聞（自備來源）'):
        title=st.text_input('標題')
        url=st.text_input('原文網址（https）')
        published=st.text_input('發布日期')
        summary=st.text_area('內容摘錄',max_chars=6000)
        if title and url.startswith('https://') and published and summary:
            news.append({'id':'USER1','title':title,'url':url,'published_at':published,'summary':summary})
    st.info('產生解讀會將標的行情、技術指標和上方新聞摘要送至AI服務；不傳送股數、成本或現金。AI不會下單。')
    model=st.text_input('OpenAI API 模型名稱',value=secret('OPENAI_MODEL'))
    key=secret('OPENAI_API_KEY')
    fingerprint=hashlib.sha256(dumps({'row':row,'news':news,'market':market,'model':model}).encode()).hexdigest()
    if st.button('產生AI情境解讀',disabled=not row.get('usable',False)):
        if not key or not model:
            st.warning('請在 Secrets 或環境變數設定 OPENAI_API_KEY 與 OPENAI_MODEL。未設定時仍可使用其他頁面。')
        else:
            try:
                from ai_analysis import explain
                with st.spinner('整理情境與引用來源…'):
                    answer=explain(row,market,news,key,model)
                st.session_state['ai_result']=(fingerprint,answer)
            except Exception:
                st.error('AI呼叫未完成或引用驗證未通過。請檢查模型權限、額度與連線；規則報表仍可使用。')
    stored=st.session_state.get('ai_result')
    if stored and stored[0]==fingerprint:
        answer=stored[1]
        st.write(answer['summary'])
        for label,k in [('偏多情境','bull_case'),('中性情境','neutral_case'),('偏空情境','bear_case')]:
            st.markdown('**'+label+'**')
            st.write(answer[k])
        if answer['events']:
            st.dataframe(pd.DataFrame(answer['events']),hide_index=True)
        st.write('待補資料：'+'；'.join(answer['missing_data']))
        st.caption('來源ID已驗證；自然語言推論與數字仍需人工核對，不作為執行指令。')
        st.download_button('下載AI分析',dumps(answer).encode(),'ai_analysis.json','application/json')
