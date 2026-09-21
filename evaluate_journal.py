"""評估V2候選訊號；固定後續交易日，不把最新價格冒充第5日價格。"""
import argparse
from datetime import datetime
from pathlib import Path
import pandas as pd
from core import completed_bars, forward_evaluation, TZ
from storage import Store
from market_data import download_bars


def evaluate_records(records, horizon=5, loader=None, now=None):
    loader=loader or download_bars
    results=[]
    history_cache={}
    # 先取得每檔最早訊號，再一次抓取，避免逐筆打API。
    eligible=[x for x in records if x['row'].get('buy_shares',0)>0]
    starts={}
    for x in eligible:
        ticker=x['row']['ticker']
        starts[ticker]=min(starts.get(ticker,x['row']['asof']),x['row']['asof'])
    for ticker,start in starts.items():
        try:
            history_cache[ticker]=completed_bars(loader(ticker,start=start,adjusted=True),now)
        except Exception:
            history_cache[ticker]=None
    for x in eligible:
        row=x['row']
        r={'訊號日期':row['asof'],'代碼':row['ticker'],'版本':x['version'],'持有交易日':horizon}
        try:
            bars=history_cache[row['ticker']]
            if bars is None:
                raise ValueError('行情暫缺')
            recorded=pd.Timestamp(x['recorded_at'])
            if recorded.tzinfo is not None:
                recorded=recorded.tz_convert('Asia/Taipei')
            # 補登旧訊號不允許回到發布前進場。最早於快照保存日之後的交易日開盤。
            effective=max(row['asof'],recorded.date().isoformat())
            s=x['settings']
            measured=forward_evaluation(bars,effective,horizon,s['fee_rate'],row['sell_tax'],s['slippage'])
            if measured is None:
                r['狀態']='尚未滿期'
            else:
                r.update({'狀態':'已滿期','試算進場日':measured['entry_date'],
                          '試算出場日':measured['exit_date'],'毛報酬(%)':measured['gross_pct'],
                          '費率後報酬(%)':measured['net_pct'],'區間最低價偏離(%)':measured['mae_pct']})
        except (ValueError,KeyError,TypeError):
            r['狀態']='資料不足，未納入統計'
        results.append(r)
    return pd.DataFrame(results)


def evaluate_performance(horizon=5, output_dir=None):
    result=evaluate_records(Store().journal(),horizon)
    if result.empty:
        print('尚無V2候選買進訊號。原Trading_Journal.csv不會覆寫或自動視為V2資料。')
        return
    output=Path(output_dir or Path(__file__).parent/'reports')
    output.mkdir(parents=True,exist_ok=True)
    path=output/f'evaluation_{horizon}d_{datetime.now(TZ).strftime("%Y%m%d_%H%M%S")}.csv'
    result.to_csv(path,index=False,encoding='utf-8-sig')
    print(result.to_string(index=False))
    valid=result[result['狀態']=='已滿期']
    if len(valid):
        positive=(valid['費率後報酬(%)']>0).mean()*100
        print(f'已滿期訊號 {len(valid)} 筆；正報酬比例 {positive:.1f}%（可能重疊，非實戰勝率）')
    print('調整價試算包含費率與滑價；不含最低手續費、整股限制與成交可行性。')
    print(f'輸出：{path.resolve()}')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--days',type=int,default=5)
    parser.add_argument('--output-dir')
    args=parser.parse_args()
    if not 1<=args.days<=60:
        parser.error('--days 須介於1與60')
    evaluate_performance(args.days,args.output_dir)
