"""行情介面：Yahoo Finance/yfinance，非交易所即時報價。"""
import yfinance as yf
from core import clean_bars


def download_bars(ticker, period='2y', start=None, adjusted=False):
    kwargs = {'start':start} if start else {'period':period}
    df = yf.download(ticker, interval='1d', auto_adjust=adjusted,
                     actions=True, progress=False, threads=False, timeout=15, **kwargs)
    return clean_bars(df)


def fetch_bundle(tickers):
    histories, errors = {}, {}
    # 使用單一工作序列，避免不同版本 yfinance 的內部共享下載狀態相互干擾。
    for ticker in list(dict.fromkeys(['^TWII']+list(tickers))):
        try:
            histories[ticker] = download_bars(ticker)
        except Exception as exc:
            errors[ticker] = f'行情取得失敗（{type(exc).__name__}），請稍後重試'
    return histories, errors


def fetch_news(ticker):
    items = yf.Ticker(ticker).get_news(count=8)
    result = []
    for item in items:
        c = item.get('content') or item
        url = c.get('canonicalUrl') or c.get('clickThroughUrl') or {}
        link = url.get('url') if isinstance(url,dict) else url
        link = link or c.get('link','')
        title = c.get('title','')
        if title and str(link).startswith('https://'):
            result.append({'id':f'N{len(result)+1}', 'title':title,
                           'published_at':c.get('pubDate') or c.get('providerPublishTime'),
                           'url':link, 'summary':c.get('summary','')[:2500]})
    return result
