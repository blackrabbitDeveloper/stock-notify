import datetime as dt
import pandas as pd
import yfinance as yf

def get_history(tickers, days=40):
    """
    여러 티커를 batch로 다운로드해도, 단일 티커여도
    항상 [Date, Close, Volume, ticker]의 'long' 포맷으로 반환.
    """
    if not tickers:
        return pd.DataFrame(columns=["Date", "Close", "Volume", "ticker"])

    # yfinance는 쉼표로 join하거나 리스트 그대로 줘도 됨
    end = dt.datetime.utcnow()
    start = end - dt.timedelta(days=days)

    df = yf.download(
        tickers=tickers,
        start=start.date(),
        end=end.date(),
        interval="1d",
        group_by="ticker",   # 멀티인덱스 컬럼이 올 수 있음
        auto_adjust=False,
        progress=False,
        threads=True,
    )

    if df is None or df.empty:
        return pd.DataFrame(columns=["Date", "Close", "Volume", "ticker"])

    # case 1) 다중 티커 → MultiIndex 컬럼
    if isinstance(df.columns, pd.MultiIndex):
        # df.columns: (field, ticker) 또는 (ticker, field) 형태일 수 있어 둘 다 대비
        level0 = [str(c) for c in df.columns.get_level_values(0)]
        level1 = [str(c) for c in df.columns.get_level_values(1)]

        # 어떤 축이 field 인지 판단
        # 샘플 로그처럼 ('Close','AAPL')면 level0=field
        if "Close" in level0 and "Volume" in level0:
            field_level = 0
            ticker_level = 1
        # 반대 ('AAPL','Close')면 level1=field
        elif "Close" in level1 and "Volume" in level1:
            field_level = 1
            ticker_level = 0
        else:
            # 구조를 모르면 안전하게 per-ticker로 다시 시도
            return _slow_per_ticker(tickers, start, end)

        frames = []
        df = df.copy()
        df = df.reset_index()  # Date가 인덱스 → 컬럼
        for t in tickers:
            # 멀티컬럼에서 해당 티커의 Close/Volume만 추출
            try:
                if field_level == 0:
                    c = df[("Close", t)]
                    v = df[("Volume", t)]
                else:
                    c = df[(t, "Close")]
                    v = df[(t, "Volume")]
                out = pd.DataFrame({
                    "Date": df["Date"],
                    "Close": c,
                    "Volume": v,
                    "ticker": t
                }).dropna(subset=["Close", "Volume"])
                out = out[out["Close"] > 0]
                frames.append(out)
            except KeyError:
                # 해당 티커 컬럼이 없으면 스킵
                continue

        if not frames:
            return pd.DataFrame(columns=["Date", "Close", "Volume", "ticker"])
        return pd.concat(frames, axis=0, ignore_index=True)

    # case 2) 단일 티커 → 일반 컬럼
    else:
        # 단일 티커 문자열/리스트 모두 대비
        t = tickers[0] if isinstance(tickers, (list, tuple)) else tickers
        out = df.reset_index()[["Date", "Close", "Volume"]].copy()
        out["ticker"] = t
        out = out.dropna(subset=["Close", "Volume"])
        out = out[out["Close"] > 0]
        return out

def _slow_per_ticker(tickers, start, end):
    """멀티컬럼 구조가 예상과 다를 때 per-ticker 루트로 안전하게."""
    rows = []
    for t in tickers:
        try:
            d = yf.download(t, start=start.date(), end=end.date(),
                            interval="1d", progress=False, auto_adjust=False)
            if d is None or d.empty:
                continue
            g = d.reset_index()[["Date", "Close", "Volume"]].copy()
            g["ticker"] = t
            g = g.dropna(subset=["Close", "Volume"])
            g = g[g["Close"] > 0]
            rows.append(g)
        except Exception:
            continue
    if not rows:
        return pd.DataFrame(columns=["Date", "Close", "Volume", "ticker"])
    return pd.concat(rows, axis=0, ignore_index=True)


def get_latest_quotes(tickers, prepost=True):
    """
    상위 N개 티커에 대해 가장 최근 체결가와 직전 종가를 반환.
    return: DataFrame[ticker, last_price, last_time, prev_close]
    """
    if not tickers:
        return pd.DataFrame(columns=["ticker","last_price","last_time","prev_close"])

    # 1분봉 최근 데이터(프리/애프터마켓 포함 가능)
    intr = yf.download(
        tickers=tickers,
        period="1d", interval="1m", prepost=prepost,
        group_by="ticker", progress=False, threads=True,
    )
    # 직전 종가(비교용)
    daily = yf.download(
        tickers=tickers,
        period="5d", interval="1d",
        group_by="ticker", progress=False, threads=True,
    )

    rows = []
    def _latest_from_series(s):
        # 마지막 NaN 제거 후 가장 최근 값/시각
        s = s.dropna()
        if s.empty: return None, None
        return float(s.iloc[-1]), s.index[-1].to_pydatetime()

    if isinstance(intr.columns, pd.MultiIndex):
        # 멀티티커 케이스
        # intr: (field, ticker) 또는 (ticker, field) 모두 대비
        lv0 = [str(x) for x in intr.columns.get_level_values(0)]
        lv1 = [str(x) for x in intr.columns.get_level_values(1)]
        field_on_0 = "Close" in lv0
        for t in tickers:
            try:
                close_ser = intr[("Close", t)] if field_on_0 else intr[(t, "Close")]
            except KeyError:
                close_ser = pd.Series(dtype=float)
            last_price, last_time = _latest_from_series(close_ser)

            # prev close
            try:
                d = daily[("Close", t)] if ("Close" in [str(x) for x in daily.columns.get_level_values(0)]) else daily[(t, "Close")]
            except Exception:
                d = pd.Series(dtype=float)
            d = d.dropna()
            prev_close = float(d.iloc[-1]) if not d.empty else None

            rows.append({"ticker": t, "last_price": last_price, "last_time": last_time, "prev_close": prev_close})
    else:
        # 단일 티커 케이스
        t = tickers[0]
        close_ser = intr["Close"] if "Close" in intr.columns else pd.Series(dtype=float)
        last_price, last_time = _latest_from_series(close_ser)
        d = daily["Close"] if "Close" in daily.columns else pd.Series(dtype=float)
        d = d.dropna()
        prev_close = float(d.iloc[-1]) if not d.empty else None
        rows.append({"ticker": t, "last_price": last_price, "last_time": last_time, "prev_close": prev_close})

    out = pd.DataFrame(rows)
    return out