"""單一私人帳戶；SQLite 本機保存，DATABASE_URL 可切換持久 PostgreSQL。"""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine, text
from core import TZ, normalize_ticker
import math

DEFAULTS = {
 'positions': [
  {'ticker':'2345.TW','name':'智邦','shares':0,'cost':0.0,'mode':'波段','max_weight':15.0,'sell_tax':0.003,'confirmed':False},
  {'ticker':'0050.TW','name':'元大台灣50','shares':2000,'cost':180.5,'mode':'長期','max_weight':50.0,'sell_tax':0.001,'confirmed':False},
  {'ticker':'00919.TW','name':'群益台灣精選高息','shares':5000,'cost':24.2,'mode':'長期','max_weight':35.0,'sell_tax':0.001,'confirmed':False},
  {'ticker':'2330.TW','name':'台積電','shares':0,'cost':0.0,'mode':'波段','max_weight':15.0,'sell_tax':0.003,'confirmed':False},
  {'ticker':'3231.TW','name':'緯創（原檔舉例）','shares':1000,'cost':105.0,'mode':'波段','max_weight':15.0,'sell_tax':0.003,'confirmed':False}],
 'settings':{'cash':0.0,'risk_pct':1.0,'stop_pct':8.0,'min_score':70,
             'fee_rate':0.001425,'min_fee':20.0,'slippage':0.001}}


def validate_config(config):
    if not isinstance(config, dict) or not isinstance(config.get('positions'), list):
        raise ValueError('設定需包含 positions 陣列與 settings')
    if len(config['positions']) > 100:
        raise ValueError('個人版本最多100檔標的')
    out, seen = [], set()
    for raw in config['positions']:
        p = dict(raw)
        p['ticker'] = normalize_ticker(p['ticker'])
        if p['ticker'] in seen:
            raise ValueError(f"重複標的：{p['ticker']}")
        seen.add(p['ticker'])
        for field in ['shares','cost','max_weight','sell_tax']:
            p[field] = float(p[field])
            if not math.isfinite(p[field]) or p[field] < 0:
                raise ValueError(f'{field} 必須是非負有限數字')
        if not p['shares'].is_integer():
            raise ValueError('股數須為整數；1張請填1000股')
        p['shares'] = int(p['shares'])
        if p['shares'] > 0 and p['cost'] <= 0:
            raise ValueError('持有股票的平均成本必須大於0')
        if not 0 < p['max_weight'] <= 100 or not 0 <= p['sell_tax'] <= 0.1:
            raise ValueError('配置上限應在0～100%之間；稅率以小數輸入')
        if p.get('mode') not in ['長期','波段']:
            raise ValueError('用途須為長期或波段')
        if not isinstance(p.get('confirmed'), bool):
            raise ValueError('確認欄位需為布林值')
        p['name'] = str(p.get('name',''))[:50]
        out.append({k:p[k] for k in DEFAULTS['positions'][0]})
    s = dict(config['settings'])
    for k in DEFAULTS['settings']:
        s[k] = float(s[k])
        if not math.isfinite(s[k]) or s[k] < 0:
            raise ValueError(f'{k} 必須是非負有限數字')
    if not 0 < s['risk_pct'] <= 5 or not 0 < s['stop_pct'] <= 50 or not 0 < s['min_score'] <= 100:
        raise ValueError('風險預算需在0～5%、停損在0～50%、分數在0～100')
    if s['fee_rate'] > 0.1 or s['slippage'] > 0.1:
        raise ValueError('費率與滑價須以小數填寫，且不大於0.1')
    return {'positions':out, 'settings':{k:s[k] for k in DEFAULTS['settings']}}


def dumps(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, default=str)


class Store:
    def __init__(self, url=None):
        url = url or os.environ.get('DATABASE_URL')
        if not url:
            root = Path(os.environ.get('QUANT_DATA_DIR', Path(__file__).parent/'data'))
            root.mkdir(parents=True, exist_ok=True)
            url = 'sqlite:///'+str((root/'quant.db').resolve())
        if url.startswith('postgres://'):
            url = url.replace('postgres://','postgresql+psycopg://',1)
        elif url.startswith('postgresql://'):
            url = url.replace('postgresql://','postgresql+psycopg://',1)
        self.engine = create_engine(url, pool_pre_ping=True)
        self.persistent_remote = not url.startswith('sqlite')
        with self.engine.begin() as c:
            c.execute(text('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL)'))
            c.execute(text('CREATE TABLE IF NOT EXISTS journal (id TEXT PRIMARY KEY, signal_date TEXT NOT NULL, ticker TEXT NOT NULL, body TEXT NOT NULL)'))
            c.execute(text('INSERT INTO config(id,body,revision) VALUES(1,:body,1) ON CONFLICT(id) DO NOTHING'), {'body':dumps(DEFAULTS)})

    def load(self):
        with self.engine.connect() as c:
            row = c.execute(text('SELECT body,revision FROM config WHERE id=1')).one()
        return json.loads(row[0]), row[1]

    def save(self, config, revision):
        config = validate_config(config)
        with self.engine.begin() as c:
            r = c.execute(text('UPDATE config SET body=:body,revision=revision+1 WHERE id=1 AND revision=:rev'),
                          {'body':dumps(config),'rev':revision})
            if r.rowcount != 1:
                raise ValueError('設定已由其他視窗更新，請重新整理再儲存')
        return config

    def record(self, rows, config, market):
        from core import VERSION
        count = 0
        with self.engine.begin() as c:
            for row in rows:
                if not row.get('asof') or not row.get('usable') or row.get('score') is None:
                    continue
                # 同一天相同標的/策略保留第一次快照，重跑不覆寫歷史。
                key = hashlib.sha256(f"{row['asof']}|{row['ticker']}|{VERSION}".encode()).hexdigest()
                body = {'version':VERSION,'recorded_at':datetime.now(TZ).isoformat(),
                        'row':row,'settings':config['settings'],'market':market}
                r = c.execute(text('INSERT INTO journal(id,signal_date,ticker,body) VALUES(:id,:date,:ticker,:body) ON CONFLICT(id) DO NOTHING'),
                              {'id':key,'date':row['asof'],'ticker':row['ticker'],'body':dumps(body)})
                count += max(0,r.rowcount)
        return count

    def journal(self):
        with self.engine.connect() as c:
            return [dict(id=r[0], **json.loads(r[1])) for r in c.execute(text('SELECT id,body FROM journal ORDER BY signal_date,ticker'))]
