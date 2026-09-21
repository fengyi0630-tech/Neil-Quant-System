"""共用規則引擎。無網路依賴，網頁與排程共用同一套計算。"""
from datetime import datetime, time
from zoneinfo import ZoneInfo
import math
import re
import numpy as np
import pandas as pd

VERSION = '2.0.0'
TZ = ZoneInfo('Asia/Taipei')


def normalize_ticker(value):
    ticker = str(value).strip().upper()
    if not re.fullmatch(r'[0-9]{4,6}[A-Z]?(?:\.TW|\.TWO)', ticker):
        raise ValueError('代碼須包含市場，例如 0050.TW、6488.TWO；不可省略開頭的 0')
    return ticker


def clean_bars(frame):
    if frame is None or frame.empty:
        raise ValueError('行情來源未回傳資料')
    df = frame.copy()
    if isinstance(df.columns, pd.MultiIndex):
        price_level = next((i for i in range(df.columns.nlevels)
                            if 'Close' in df.columns.get_level_values(i)), None)
        if price_level is None:
            raise ValueError('行情欄位格式無法辨識')
        df.columns = df.columns.get_level_values(price_level)
    if df.columns.duplicated().any():
        raise ValueError('請一次傳入單一標的行情')
    columns = ['Open', 'High', 'Low', 'Close', 'Volume']
    if not set(columns).issubset(df.columns):
        raise ValueError('行情缺少 OHLCV 欄位')
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index))
    if df.index.tz is not None:
        df.index = df.index.tz_convert('Asia/Taipei').tz_localize(None)
    df.index = df.index.normalize()
    df = df[~df.index.duplicated(keep='last')].sort_index()
    df[columns] = df[columns].apply(pd.to_numeric, errors='coerce')
    if not np.isfinite(df[columns].to_numpy(dtype=float)).all():
        raise ValueError('行情含空值或非有限值，暫停計算')
    if (df[columns[:4]] <= 0).any().any() or (df.Volume < 0).any():
        raise ValueError('行情含不合理的價格或成交量')
    if ((df.High < df[['Open', 'Close', 'Low']].max(axis=1)) |
            (df.Low > df[['Open', 'Close', 'High']].min(axis=1))).any():
        raise ValueError('行情高低價順序異常')
    return df


def completed_bars(frame, now=None):
    """保守地在台北 15:00 後採用當天日線；不是交易所行事曆。"""
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    now = now.astimezone(TZ)
    df = clean_bars(frame)
    today = pd.Timestamp(now.date())
    df = df[df.index <= today] if now.time() >= time(15) else df[df.index < today]
    if df.empty:
        raise ValueError('尚無可用的完整日線')
    return df


def data_quality(frame, now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    now = now.astimezone(TZ)
    age = (now.date() - frame.index[-1].date()).days
    # 長假可能保守誤報；不把無法核實的舊行情視為可交易。
    if age > 4:
        return False, f'行情距今 {age} 個日曆日；可能過期或遇長假，需核對'
    if frame.Volume.iloc[-1] <= 0:
        return False, '最新日線成交量為零，暫停訊號'
    return True, '已排除今日未完成日線；未核對官方休市日曆'


def indicators(frame):
    df = clean_bars(frame)
    c = df.Close
    df['MA20'] = c.rolling(20).mean()
    df['MA60'] = c.rolling(60).mean()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    df['RSI14'] = (100 - 100/(1 + up/down)).mask((up == 0) & (down == 0), 50)
    prev = c.shift(1)
    tr = pd.concat([df.High-df.Low, (df.High-prev).abs(), (df.Low-prev).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    df['OBV'] = (np.sign(delta).fillna(0)*df.Volume).cumsum()
    df['Support20'] = df.Low.shift(1).rolling(20).min()
    df['Resistance20'] = df.High.shift(1).rolling(20).max()
    df['VolumeRatio'] = df.Volume / df.Volume.shift(1).rolling(5).mean().replace(0, np.nan)
    df['Bias20'] = 100*(c/df.MA20-1)
    df['OBVBreakout'] = df.OBV > df.OBV.shift(1).rolling(20).max()
    return df


def score_latest(df):
    if len(df) < 65:
        raise ValueError('至少需要 65 根完整日線')
    x = df.iloc[-1]
    needed = ['MA20','MA60','RSI14','ATR14','Support20','Resistance20','VolumeRatio']
    if not np.isfinite(x[needed].astype(float)).all():
        raise ValueError('指標資料不足')
    score, reasons = 0, []
    checks = [(x.Close > x.MA20, 20, '收盤高於20日線'),
              (x.MA20 > x.MA60, 20, '20日線高於60日線'),
              (x.MA60 > df.MA60.iloc[-6], 15, '60日線上彎'),
              (45 <= x.RSI14 <= 70, 15, 'RSI位於45～70'),
              (x.Close > x.Resistance20, 15, '突破前20日高點'),
              (bool(x.OBVBreakout) and x.VolumeRatio > 1.2, 15, 'OBV創高且放量')]
    for passed, weight, label in checks:
        if passed:
            score += weight
            reasons.append(label)
    if x.Bias20 > 12:
        score -= 20
        reasons.append('正乖離過大，扣20分')
    return max(0, score), '；'.join(reasons) or '尚無符合的偏多條件'


def market_state(frame, now=None):
    if frame is None or frame.empty:
        return {'state':'unknown', 'message':'大盤資料缺失，停止新增部位建議', 'asof':None}
    try:
        df = completed_bars(frame, now)
        if len(df) < 20:
            raise ValueError('大盤不足20根日線')
        ok, note = data_quality(df, now)
        if not ok:
            raise ValueError(note)
        above = bool(df.Close.iloc[-1] >= df.Close.tail(20).mean())
        return {'state':'above' if above else 'below',
                'message':'加權指數高於20日線' if above else '加權指數低於20日線',
                'asof':df.index[-1].date().isoformat()}
    except ValueError as exc:
        return {'state':'unknown', 'message':str(exc), 'asof':None}


def affordable_shares(budget, price, rate, minimum, cap=None):
    if budget <= 0 or price <= 0:
        return 0
    n = max(0, math.floor(min(budget / (price*(1+rate)), (budget-minimum)/price)))
    return min(n, int(cap)) if cap is not None else n


def analyze_portfolio(positions, settings, histories, market, now=None):
    rows, charts = [], {}
    for p in positions:
        row = dict(p, price=None, asof=None, market_value=None, pnl=None, pnl_pct=None,
                   score=None, action='資料不足', buy_shares=0, buy_budget=0.0,
                   weight=None, stop=None, target=None, quality='', evidence='')
        try:
            df = indicators(completed_bars(histories.get(p['ticker']), now))
            charts[p['ticker']] = df
            latest = df.iloc[-1]
            ok, note = data_quality(df, now)
            row.update(price=float(latest.Close), asof=df.index[-1].date().isoformat(),
                       market_value=float(latest.Close)*p['shares'], quality=note, usable=ok)
            if p['confirmed'] and p['shares'] > 0:
                row.update(pnl=(float(latest.Close)-p['cost'])*p['shares'],
                           pnl_pct=100*(float(latest.Close)/p['cost']-1))
            score, evidence = score_latest(df)
            row.update(score=score, evidence=evidence, atr=float(latest.ATR14),
                       support=float(latest.Support20), resistance=float(latest.Resistance20),
                       rsi=float(latest.RSI14), ma20=float(latest.MA20), ma60=float(latest.MA60))
            # 參考價由模型計算，並非預測；尚未依各商品跳動單位轉為委託價。
            row['stop'] = max(0.01, float(latest.Close)-2*float(latest.ATR14))
            row['target'] = float(latest.Close)+4*float(latest.ATR14)
        except (ValueError, TypeError, KeyError) as exc:
            row['quality'] = str(exc)
            row['usable'] = False
        rows.append(row)
    held = [r for r in rows if r['shares'] > 0]
    complete = all(r['confirmed'] and r.get('usable',False) for r in held)
    dates = {r['asof'] for r in held}
    complete = complete and len(dates) <= 1
    values = [r['market_value'] for r in held if r['market_value'] is not None]
    known_value = sum(values)
    equity = settings['cash']+known_value if complete else None
    remaining_cash = settings['cash']
    risk_left = equity*settings['risk_pct']/100 if equity is not None else 0
    # 依分數排序分配同一筆現金與整批風險預算，避免每一檔重複使用現金。
    for r in sorted(rows, key=lambda x: x['score'] if x['score'] is not None else -1, reverse=True):
        if equity and r['market_value'] is not None:
            r['weight'] = 100*r['market_value']/equity
        if not r.get('usable') or r['score'] is None:
            r['action'] = '資料不足／暫停建議'
        elif r['shares'] > 0 and not r['confirmed']:
            r['action'] = '先確認股數與成本'
        elif r['mode'] == '波段' and r['pnl_pct'] is not None and r['pnl_pct'] <= -settings['stop_pct']:
            r['action'] = '觸發成本停損檢視（需人工確認）'
        elif not complete:
            r['action'] = '帳本未完整，暫停新增部位'
        elif r['weight'] is not None and r['weight'] > r['max_weight']:
            r['action'] = '配置超限，檢視再平衡'
        elif r['mode'] == '長期':
            r['action'] = '長期配置檢視；不套用短線買賣規則'
        elif market['state'] == 'unknown' or market.get('asof') != r['asof']:
            r['action'] = '大盤資料不足／日期不同，暫停新增部位'
        elif market['state'] == 'below':
            r['action'] = '大盤偏弱，暫停新增部位'
        elif r['score'] >= settings['min_score']:
            if not equity or equity <= 0:
                r['action'] = '請設定可用現金'
                continue
            headroom = max(0, equity*r['max_weight']/100-r['market_value'])
            risk_per_share = r['price']-r['stop'] + r['price']*(2*settings['fee_rate']+r['sell_tax']+2*settings['slippage'])
            max_by_risk = math.floor(max(0, risk_left-2*settings['min_fee'])/risk_per_share)
            entry_price = r['price']*(1+settings['slippage'])
            n = affordable_shares(remaining_cash, entry_price, settings['fee_rate'],
                                  settings['min_fee'], min(max_by_risk, math.floor(headroom/entry_price)))
            r['buy_shares'] = n
            if n > 0:
                budget = n*entry_price+max(settings['min_fee'], n*entry_price*settings['fee_rate'])
                r['buy_budget'] = budget
                remaining_cash -= budget
                risk_left = max(0, risk_left-n*risk_per_share-2*settings['min_fee'])
                r['action'] = '符合候選条件：次日重新確認價格與風險'
            else:
                r['action'] = '符合訊號，但現金／配置／風險額度不足'
        else:
            r['action'] = '續抱觀察，暫無新增訊號' if r['shares'] else '空手觀察'
    return rows, charts, {'complete':complete, 'equity':equity, 'known_value':known_value,
                           'cash':settings['cash'], 'pnl':sum(r['pnl'] for r in held) if complete else None}


def forward_evaluation(frame, signal_date, horizon=5, fee_rate=0.001425, sell_tax=0.003, slippage=0.001):
    """調整價訊號研究：訊號後第一根開盤 -> 第 N 根收盤；非成交回測。"""
    if int(horizon) != horizon or horizon < 1:
        raise ValueError('持有交易日須為正整數')
    df = clean_bars(frame)
    future = df[df.index > pd.Timestamp(signal_date).normalize()]
    if len(future) < horizon:
        return None
    segment = future.iloc[:horizon]
    if (segment.Volume <= 0).any():
        raise ValueError('持有區間有零成交量，不估算成交')
    first, last = segment.iloc[0], segment.iloc[-1]
    entry = float(first.Open)*(1+slippage)
    exit_price = float(last.Close)*(1-slippage)
    gross = float(last.Close)/float(first.Open)-1
    net = exit_price*(1-fee_rate-sell_tax)/(entry*(1+fee_rate))-1
    return {'entry_date':segment.index[0].date().isoformat(),
            'exit_date':segment.index[-1].date().isoformat(), 'entry':entry, 'exit':exit_price,
            'gross_pct':100*gross, 'net_pct':100*net,
            'mae_pct':100*(float(segment.Low.min())/float(first.Open)-1)}


def backtest_ma(frame, fee_rate=0.001425, sell_tax=0.003, slippage=0.001):
    """調整價 MA20>MA60 教學回測；訊號隔日開盤交易，分數策略另由日誌追蹤。"""
    df = indicators(frame)
    if len(df) < 80:
        raise ValueError('回測至少需80根日線')
    if (df.Volume <= 0).any():
        raise ValueError('區間含零成交量，簡化回測不支援停牌情境')
    cash, units = 1.0, 0.0
    curves, trades, entry_cost, entry_date = [], [], None, None
    bh_units = 1/(float(df.Open.iloc[60])*(1+slippage)*(1+fee_rate))
    for i in range(60, len(df)):
        prev, day = df.iloc[i-1], df.iloc[i]
        want_long = prev.MA20 > prev.MA60
        if want_long and units == 0:
            entry_cost = cash
            units = cash/(float(day.Open)*(1+slippage)*(1+fee_rate))
            cash = 0.0
            entry_date = df.index[i].date().isoformat()
        elif not want_long and units > 0:
            cash = units*float(day.Open)*(1-slippage)*(1-fee_rate-sell_tax)
            trades.append({'進場日':entry_date,'出場日':df.index[i].date().isoformat(),
                           '報酬(%)':100*(cash/entry_cost-1)})
            units = 0.0
        # 以當日假設清算淨值記價，與比較基準一致；期末未平倉不計勝率。
        value = cash+units*float(day.Close)*(1-slippage)*(1-fee_rate-sell_tax)
        benchmark = bh_units*float(day.Close)*(1-slippage)*(1-fee_rate-sell_tax)
        curves.append({'日期':df.index[i], '策略':value, '買進持有':benchmark})
    curve = pd.DataFrame(curves).set_index('日期')
    drawdown = curve['策略']/curve['策略'].cummax().clip(lower=1)-1
    returns = [x['報酬(%)']/100 for x in trades]
    gains = sum(max(x,0) for x in returns)
    losses = -sum(min(x,0) for x in returns)
    stats = {'總報酬(%)':100*(curve['策略'].iloc[-1]-1),
             '買進持有報酬(%)':100*(curve['買進持有'].iloc[-1]-1),
             '最大回撤(%)':100*drawdown.min(), '已平倉次數':len(trades),
             '勝率(%)':100*sum(x>0 for x in returns)/len(returns) if returns else None,
             '獲利因子':gains/losses if losses else None, '期末持倉':bool(units)}
    return curve, pd.DataFrame(trades), stats
