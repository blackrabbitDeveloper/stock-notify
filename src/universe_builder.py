import os, re, traceback
import requests
import pandas as pd
import numpy as np
from .fetch_prices import get_history

ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(ROOT, "data", "pools")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

def _clean_symbol(s: str) -> str:
    if not s: return ""
    s = str(s).strip().upper().replace(" ", "").replace(".", "-")
    return re.sub(r"[^A-Z0-9\-]", "", s)

def _read_local_list(name: str) -> list[str]:
    path = os.path.join(DATA_DIR, f"{name}.txt")
    if not os.path.exists(path): return []
    with open(path, "r", encoding="utf-8") as f:
        syms = [_clean_symbol(line) for line in f if _clean_symbol(line)]
    # 순서 보존 중복 제거
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s); out.append(s)
    return out

def _read_symbols_from_html(html: str, symbol_like=("Symbol","Ticker")) -> list[str]:
    tables = pd.read_html(html)
    for df in tables:
        for col in df.columns:
            if any(k.lower() in str(col).lower() for k in symbol_like):
                syms = df[col].dropna().astype(str).map(_clean_symbol)
                syms = [s for s in syms if s and s != "—"]
                # 순서 보존 중복 제거
                seen, out = set(), []
                for s in syms:
                    if s not in seen:
                        seen.add(s); out.append(s)
                return out
    return []

def _fetch_html(url: str) -> str:
    # 403 회피용 UA 헤더 + 짧은 재시도
    for _ in range(2):
        r = requests.get(url, timeout=20, headers=UA)
        r.raise_for_status()
        return r.text
    return ""

def get_pool(name: str = "sp500") -> list[str]:
    name = (name or "sp500").lower()

    # 1) 로컬 캐시 우선
    local = _read_local_list(name)
    if local:
        print(f"[DEBUG] local cache hit: {len(local)}")
        return local

    # 2) 위키 (403이면 except로 빠짐)
    try:
        if name == "sp500":
            html = _fetch_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
            syms = _read_symbols_from_html(html, symbol_like=("Symbol",))
            if syms:
                print(f"[DEBUG] wiki sp500: {len(syms)}"); return syms
        elif name == "nasdaq100":
            html = _fetch_html("https://en.wikipedia.org/wiki/Nasdaq-100")
            syms = _read_symbols_from_html(html, symbol_like=("Symbol","Ticker"))
            if syms:
                print(f"[DEBUG] wiki nasdaq100: {len(syms)}"); return syms
    except Exception as e:
        traceback.print_exc()

    # 3) 최종 폴백
    fb = ["AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","BRK-B","AVGO","JPM"]
    print(f"[DEBUG] using fallback: {len(fb)}")
    return fb

def build_auto_universe(cfg):
    """
    개선된 종목 선별 로직
    - 우량주 편향 감소
    - 중소형주도 포함
    - 변동성과 모멘텀 중심 선별
    """
    pool = get_pool(cfg.get("pool", "sp500"))
    print(f"[DEBUG] pool size: {len(pool)} sample: {pool[:10]}")

    hist = get_history(pool, days=40)
    print(f"[DEBUG] hist shape: {hist.shape} cols: {list(hist.columns)}")
    if hist.empty:
        print("[DEBUG] hist empty → fallback to first 20 from pool")
        return pool[:20]

    # 필수 컬럼, 결측 제거
    hist = hist.dropna(subset=["Close", "Volume"])
    hist = hist[hist["Close"] > 0]

    # per-ticker 개수 로그
    cnt = hist.groupby("ticker").size().sort_values(ascending=False)
    print(f"[DEBUG] per-ticker rows (top 10):\n{cnt.head(10)}")

    if hist.empty:
        print("[DEBUG] hist became empty after cleaning → fallback")
        return pool[:20]

    def feats(g: pd.DataFrame):
        g = g.sort_values("Date").tail(20)
        if g.empty:
            return pd.Series({
                "adv": float("nan"), 
                "vol": float("nan"),
                "ret1d": float("nan"), 
                "ret5d": float("nan"),
                "last": float("nan"),
                "avg_vol_ratio": float("nan")
            })
        
        # 거래대금
        adv = (g["Close"] * g["Volume"]).mean()
        
        # 변동성 (최근 20일)
        vol = g["Close"].pct_change().abs().tail(20).mean() * 100.0
        
        # 수익률
        ret1d = (g["Close"].iloc[-1] / g["Close"].iloc[-2] - 1) * 100.0 if len(g) >= 2 else 0.0
        ret5d = (g["Close"].iloc[-1] / g["Close"].iloc[-6] - 1) * 100.0 if len(g) >= 6 else 0.0
        
        # 현재가
        last = g["Close"].iloc[-1]
        
        # 거래량 비율 (최근 5일 vs 이전 15일)
        if len(g) >= 20:
            recent_vol = g["Volume"].tail(5).mean()
            prev_vol = g["Volume"].iloc[-20:-5].mean()
            avg_vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1.0
        else:
            avg_vol_ratio = 1.0
        
        return pd.Series({
            "adv": adv, 
            "vol": vol, 
            "ret1d": ret1d,
            "ret5d": ret5d,
            "last": last,
            "avg_vol_ratio": avg_vol_ratio
        })

    F = hist.groupby("ticker", group_keys=False).apply(feats).reset_index()
    print(f"[DEBUG] features shape: {F.shape}")
    
    # NaN 제거
    F = F.dropna(subset=["adv", "last"])
    
    # 가격 필터 (더 낮은 하한으로 변경: $5 → $3)
    min_price = float(cfg.get("min_price", 3))
    max_price = float(cfg.get("max_price", 500))  # 고가주 제한
    F = F[(F["last"] >= min_price) & (F["last"] <= max_price)]
    print(f"[DEBUG] after price filter (${min_price}~${max_price}): {len(F)}")

    if F.empty:
        print("[DEBUG] features empty after filters → fallback")
        return pool[:20]

    # === 개선된 스코어링 시스템 ===
    
    # 1) 거래량 최소 기준 (유동성 확보)
    # ADV 하위 30% 제거
    adv_threshold = F["adv"].quantile(0.3)
    F = F[F["adv"] >= adv_threshold]
    print(f"[DEBUG] after liquidity filter (ADV >= {adv_threshold:.0f}): {len(F)}")
    
    if F.empty:
        return pool[:20]
    
    # 2) Z-score 정규화
    def safe_zscore(series):
        std = series.std(ddof=0)
        if std == 0 or pd.isna(std):
            return pd.Series([0.0] * len(series), index=series.index)
        return (series - series.mean()) / std
    
    F["z_vol"] = safe_zscore(F["vol"])          # 변동성 (높을수록 좋음)
    F["z_ret1d"] = safe_zscore(F["ret1d"])      # 단기 수익률
    F["z_ret5d"] = safe_zscore(F["ret5d"])      # 중기 수익률
    F["z_vol_ratio"] = safe_zscore(F["avg_vol_ratio"])  # 거래량 증가
    
    # ADV는 로그 스케일 후 정규화 (대형주 과도한 우대 방지)
    F["log_adv"] = F["adv"].apply(lambda x: float('nan') if x <= 0 else np.log(x))
    F["z_log_adv"] = safe_zscore(F["log_adv"])
    
    # 3) 합성 점수 (개선)
    # - 변동성과 모멘텀 중심
    # - 거래대금은 최소 기준만 충족하면 비중 낮춤
    F["score_u"] = (
        F["z_log_adv"] * 0.15 +        # 거래대금 (로그) - 비중 축소
        F["z_vol"] * 0.25 +            # 변동성 (단기 변동 기회)
        F["z_ret1d"] * 0.20 +          # 1일 모멘텀
        F["z_ret5d"] * 0.25 +          # 5일 모멘텀
        F["z_vol_ratio"] * 0.15        # 거래량 증가 추세
    )
    
    # 4) 최종 선별
    k = int(cfg.get("max_final_universe", 150))
    uni = F.sort_values("score_u", ascending=False).head(k)["ticker"].tolist()
    
    print(f"[DEBUG] final universe size: {len(uni)}")
    print(f"[DEBUG] sample tickers: {uni[:10]}")
    print(f"[DEBUG] score range: {F['score_u'].min():.2f} ~ {F['score_u'].max():.2f}")
    
    # 가격대별 분포 확인
    selected = F[F["ticker"].isin(uni)]
    print(f"[DEBUG] price distribution:")
    print(f"  - Under $20: {len(selected[selected['last'] < 20])}")
    print(f"  - $20-$50: {len(selected[(selected['last'] >= 20) & (selected['last'] < 50)])}")
    print(f"  - $50-$100: {len(selected[(selected['last'] >= 50) & (selected['last'] < 100)])}")
    print(f"  - Over $100: {len(selected[selected['last'] >= 100])}")
    
    return uni
