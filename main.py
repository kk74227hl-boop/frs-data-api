from datetime import datetime,time,timezone
from zoneinfo import ZoneInfo
from typing import Optional
import os,httpx
from fastapi import FastAPI,HTTPException,Query
from pydantic import BaseModel
API_VERSION='1.5.0'; FRS_VERSION='FRS V31.1-T1-PRO-CANONICAL'
MAX_AGE=int(os.getenv('QUOTE_MAX_AGE_SECONDS','60')); US_PRIMARY=os.getenv('US_PRIMARY_PROVIDER','TWELVE_DATA').upper()
TW_HOLIDAYS={x.strip() for x in os.getenv('TW_HOLIDAYS','').split(',') if x.strip()}; FUGLE_KEY=os.getenv('FUGLE_API_KEY',''); TWELVE_KEY=os.getenv('TWELVE_DATA_API_KEY',''); FINNHUB_KEY=os.getenv('FINNHUB_API_KEY','')
TW_OFFICIAL_CLOSURES={2026:{'2026-01-01','2026-02-16','2026-02-17','2026-02-18','2026-02-19','2026-02-20','2026-02-27','2026-04-03','2026-04-06','2026-05-01','2026-06-19','2026-09-25','2026-09-28','2026-10-09','2026-10-26','2026-12-25'}}
BLOCKED=['STALE','OFFICIAL_CLOSE','MARKET_CLOSED','API_ERROR','UNAVAILABLE']
class Quote(BaseModel):
 symbol:str; market:str; price:Optional[float]=None; open:Optional[float]=None; high:Optional[float]=None; low:Optional[float]=None; prev_close:Optional[float]=None; volume:Optional[float]=None; turnover:Optional[float]=None; vwap:Optional[float]=None; vwap_method:Optional[str]=None; session:str; market_status:str; source:str; provider_timestamp:Optional[datetime]=None; api_received_timestamp:datetime; data_age_seconds:Optional[float]=None; status:str; price_type:str; t0_allowed:bool; stale_after_seconds:int=MAX_AGE
def num(v):
 try:return float(v) if v is not None else None
 except:return None
def market_status(market,now=None):
 now=now or datetime.now(timezone.utc); m=market.upper()
 if m=='TW':
  l=now.astimezone(ZoneInfo('Asia/Taipei')); ds=l.date().isoformat()
  if l.weekday()>=5:return 'MARKET_CLOSED','OFFICIAL_CLOSE','WEEKEND'
  if ds in TW_HOLIDAYS:return 'MARKET_CLOSED','OFFICIAL_CLOSE','MANUAL_HOLIDAY_OVERRIDE'
  if ds in TW_OFFICIAL_CLOSURES.get(l.year,set()):return 'MARKET_CLOSED','OFFICIAL_CLOSE','OFFICIAL_HOLIDAY'
  if time(8,30)<=l.time()<time(13,30):return 'OPEN','REGULAR','TRADING_SESSION'
  return 'MARKET_CLOSED','OFFICIAL_CLOSE','OUTSIDE_REGULAR_SESSION'
 if m=='US':
  l=now.astimezone(ZoneInfo('America/New_York'))
  if l.weekday()>=5:return 'MARKET_CLOSED','OFFICIAL_CLOSE','WEEKEND'
  if time(9,30)<=l.time()<time(16):return 'OPEN','REGULAR','TRADING_SESSION'
  if time(4)<=l.time()<time(9,30):return 'OPEN','PRE_MARKET','PRE_MARKET'
  if time(16)<=l.time()<time(20):return 'OPEN','AFTER_HOURS','AFTER_HOURS'
  return 'MARKET_CLOSED','OFFICIAL_CLOSE','OUTSIDE_REGULAR_SESSION'
 return 'UNKNOWN','OFFLINE','UNSUPPORTED_MARKET'
def ts(v):
 try:return datetime.fromtimestamp(float(v),tz=timezone.utc) if v is not None else None
 except:return None
async def twelve(symbol):
 if not TWELVE_KEY:raise RuntimeError('TWELVE_DATA_API_KEY is not configured')
 received=datetime.now(timezone.utc)
 async with httpx.AsyncClient(timeout=10) as c:r=await c.get('https://api.twelvedata.com/quote',params={'symbol':symbol.upper(),'apikey':TWELVE_KEY,'prepost':'true'})
 r.raise_for_status(); q=r.json()
 if q.get('status')=='error' or q.get('code'):raise RuntimeError(q.get('message') or 'Twelve Data API error')
 p= num(q.get('close')); pt=ts(q.get('timestamp')); state,session,_=market_status('US',received); age=(received-pt).total_seconds() if pt else None; live=state=='OPEN' and p is not None and age is not None and 0<=age<=MAX_AGE; status='LIVE_VERIFIED' if live else ('MARKET_CLOSED' if state=='MARKET_CLOSED' else 'STALE')
 return Quote(symbol=symbol.upper(),market='US',price=p,open=num(q.get('open')),high=num(q.get('high')),low=num(q.get('low')),prev_close=num(q.get('previous_close')),volume=num(q.get('volume')),session=session,market_status=state,source='TWELVE_DATA',provider_timestamp=pt,api_received_timestamp=received,data_age_seconds=age,status=status,price_type='LIVE' if live else 'HISTORICAL',t0_allowed=live)
async def finnhub(symbol):
 if not FINNHUB_KEY:raise RuntimeError('FINNHUB_API_KEY is not configured')
 received=datetime.now(timezone.utc)
 async with httpx.AsyncClient(timeout=10) as c:r=await c.get('https://finnhub.io/api/v1/quote',params={'symbol':symbol.upper(),'token':FINNHUB_KEY})
 r.raise_for_status(); q=r.json(); pt=ts(q.get('t')); state,session,_=market_status('US',received); age=(received-pt).total_seconds() if pt else None; p=num(q.get('c')); live=state=='OPEN' and p is not None and age is not None and 0<=age<=MAX_AGE; status='LIVE_VERIFIED' if live else ('MARKET_CLOSED' if state=='MARKET_CLOSED' else 'STALE')
 return Quote(symbol=symbol.upper(),market='US',price=p,open=num(q.get('o')),high=num(q.get('h')),low=num(q.get('l')),prev_close=num(q.get('pc')),session=session,market_status=state,source='FINNHUB',provider_timestamp=pt,api_received_timestamp=received,data_age_seconds=age,status=status,price_type='LIVE' if live else 'HISTORICAL',t0_allowed=live)
async def fugle(symbol):
 if not FUGLE_KEY:raise RuntimeError('FUGLE_API_KEY is not configured')
 received=datetime.now(timezone.utc)
 async with httpx.AsyncClient(timeout=10) as c:r=await c.get('https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote',params={'symbol':symbol.upper(),'apiToken':FUGLE_KEY})
 r.raise_for_status(); q=r.json(); d=q.get('data',q); raw=d.get('date') or d.get('timestamp') or d.get('lastUpdated'); pt=None
 if raw:
  try:pt=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
  except:pass
 p=num(d.get('price') or d.get('close')); state,session,_=market_status('TW',received); age=(received-pt).total_seconds() if pt else None; live=state=='OPEN' and p is not None and age is not None and 0<=age<=MAX_AGE; status='LIVE_VERIFIED' if live else ('MARKET_CLOSED' if state=='MARKET_CLOSED' else 'STALE')
 return Quote(symbol=symbol.upper(),market='TW',price=p,open=num(d.get('openPrice') or d.get('open')),high=num(d.get('highPrice') or d.get('high')),low=num(d.get('lowPrice') or d.get('low')),prev_close=num(d.get('previousClose') or d.get('prevClose')),volume=num(d.get('volume')),turnover=num(d.get('turnover')),vwap=num(d.get('avgPrice') or d.get('vwap')),session=session,market_status=state,source='FUGLE',provider_timestamp=pt,api_received_timestamp=received,data_age_seconds=age,status=status,price_type='LIVE' if live else 'HISTORICAL',t0_allowed=live)
async def get_quote(market,symbol):
 m=market.upper()
 if m=='TW':return await fugle(symbol)
 if m=='US':
  if US_PRIMARY in ('TWELVE','TWELVE_DATA') and TWELVE_KEY:return await twelve(symbol)
  return await finnhub(symbol)
 raise ValueError(f'Unsupported market: {market}')
app=FastAPI(title='FRS Data API',version=API_VERSION)
@app.get('/health')
async def health():return {'status':'ok','service':'FRS Data API','version':API_VERSION,'timestamp':datetime.now(timezone.utc).isoformat()}
@app.get('/v1/quote/{market}/{symbol}',response_model=Quote)
async def quote(market,symbol):
 try:return await get_quote(market,symbol)
 except Exception as e:raise HTTPException(502,str(e))
@app.get('/v1/quotes')
async def quotes(market:str=Query(...),symbols:str=Query(...)):
 out=[]
 for s in [x.strip() for x in symbols.split(',') if x.strip()]:
  try:out.append((await get_quote(market,s)).model_dump(mode='json'))
  except Exception as e:out.append({'symbol':s,'market':market.upper(),'status':'API_ERROR','t0_allowed':False,'error':str(e)})
 return {'market':market.upper(),'count':len(out),'quotes':out}
@app.get('/v1/market-status')
async def status(market:str):
 now=datetime.now(timezone.utc);s,session,reason=market_status(market,now);return {'market':market.upper(),'market_status':s,'session':session,'reason':reason,'as_of':now}
@app.get('/v1/diagnostics')
async def diagnostics():return {'api_version':API_VERSION,'frs_version':FRS_VERSION,'providers':{'US':{'primary':US_PRIMARY,'TWELVE_DATA':{'configured':bool(TWELVE_KEY)},'FINNHUB':{'configured':bool(FINNHUB_KEY)}},'TW':{'name':'FUGLE','configured':bool(FUGLE_KEY)}},'quote_max_age_seconds':MAX_AGE,'t0_requires':'LIVE_VERIFIED','blocked_statuses':BLOCKED}
@app.get('/v1/frs-context')
async def frs_context(symbol:str,market:str):
 try:q=await get_quote(market,symbol)
 except Exception as e:raise HTTPException(502,str(e))
 executable=q.status=='LIVE_VERIFIED' and q.t0_allowed
 return {'frs_version':FRS_VERSION,'symbol':symbol.upper(),'market':market.upper(),'quote':q.model_dump(mode='json'),'t0_allowed':executable,'execution_gate':{'status':q.status,'executable_t0':executable,'blocked_statuses':BLOCKED},'hard_rule':'Only LIVE_VERIFIED may be used as an executable FRS T0 live price.'}
