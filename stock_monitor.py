"""收盤後排程入口。與 app.py 使用同一資料庫與核心規則；不發送訊息或下單。"""
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
from core import TZ, VERSION, market_state, analyze_portfolio
from storage import Store, validate_config, dumps
from market_data import fetch_bundle


def run_manager(output_dir=None, with_ai=False):
    store=Store()
    config,_=store.load()
    config=validate_config(config)
    histories,errors=fetch_bundle([p['ticker'] for p in config['positions']])
    market=market_state(histories.get('^TWII'))
    rows,_,totals=analyze_portfolio(config['positions'],config['settings'],histories,market)
    output=Path(output_dir or Path(__file__).parent/'reports')
    output.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(TZ).strftime('%Y%m%d_%H%M%S')
    pd.DataFrame(rows).to_csv(output/f'report_{stamp}.csv',index=False,encoding='utf-8-sig')
    snapshot={'version':VERSION,'created_at':datetime.now(TZ).isoformat(),
              'market':market,'totals':totals,'errors':errors,'rows':rows}
    (output/f'report_{stamp}.json').write_text(dumps(snapshot),encoding='utf-8')
    count=store.record(rows,config,market)
    print(market['message'])
    if rows:
        print(pd.DataFrame(rows)[['ticker','asof','score','action','buy_shares']].to_string(index=False))
    print(f'保存 {count} 筆新快照；報告位置：{output.resolve()}')
    if with_ai:
        import os
        from ai_analysis import explain
        from market_data import fetch_news
        if not os.environ.get('OPENAI_API_KEY') or not os.environ.get('OPENAI_MODEL'):
            print('AI跳過：缺少 OPENAI_API_KEY 或 OPENAI_MODEL')
        else:
            for row in rows:
                if not row.get('usable'):
                    continue
                try:
                    news=fetch_news(row['ticker'])
                    answer=explain(row,market,news,os.environ['OPENAI_API_KEY'],os.environ['OPENAI_MODEL'])
                    payload={'asof':row['asof'],'ticker':row['ticker'],'news':news,'analysis':answer}
                    (output/f"ai_{row['ticker']}_{stamp}.json").write_text(dumps(payload),encoding='utf-8')
                except Exception as exc:
                    print(f"{row['ticker']} AI跳過（{type(exc).__name__}）")
    return 1 if errors or market['state']=='unknown' else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='NEIL QUANT 收盤後分析')
    parser.add_argument('--output-dir')
    parser.add_argument('--ai',action='store_true',help='額外呼叫AI，需環境變數金鑰與模型，會產生API用量')
    args=parser.parse_args()
    raise SystemExit(run_manager(args.output_dir,args.ai))
