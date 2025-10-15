import os, re, traceback
import requests
import pandas as pd
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
    pool = get_pool(cfg.get("pool", "sp500"))
    print(f"[DEBUG] pool size: {len(pool)} sample: {pool[:10]}")

    # 풀 너무 크면 초반 테스트용으로 제한 (선택)
    # pool = pool[:200]

    hist = get_history(pool, days=40)
    print(f"[DEBUG] hist shape: {hist.shape} cols: {list(hist.columns)}")
    if hist.empty:
        print("[DEBUG] hist empty → fallback to first 20 from pool")
        return pool[:20]  # 최소 폴백으로라도 진행

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
            return pd.Series({"adv": float("nan"), "vol": float("nan"),
                              "ret1d": float("nan"), "last": float("nan")})
        adv = (g["Close"] * g["Volume"]).mean()
        vol = g["Close"].pct_change().abs().tail(20).mean() * 100.0
        ret1d = (g["Close"].iloc[-1] / g["Close"].iloc[-2] - 1) * 100.0 if len(g) >= 2 else 0.0
        last = g["Close"].iloc[-1]
        return pd.Series({"adv": adv, "vol": vol, "ret1d": ret1d, "last": last})

    F = hist.groupby("ticker", group_keys=False).apply(feats).reset_index()
    print(f"[DEBUG] features shape: {F.shape}")
    # NaN 제거
    F = F.dropna(subset=["adv", "last"])
    # 가격 하한
    min_price = float(cfg.get("min_price", 5))
    F = F[F["last"] >= min_price]
    print(f"[DEBUG] after price filter (>= {min_price}): {len(F)}")

    if F.empty:
        print("[DEBUG] features empty after filters → fallback")
        return pool[:20]

    # 거래대금 상위 컷
    m = int(cfg.get("top_by_dollar_vol", 200))
    F = F.sort_values("adv", ascending=False).head(m)

    # 합성 스코어
    F["rank_adv"] = F["adv"].rank(ascending=False, method="min")
    F["z_vol"] = (F["vol"] - F["vol"].mean()) / (F["vol"].std(ddof=0) + 1e-9)
    F["z_ret"] = (F["ret1d"] - F["ret1d"].mean()) / (F["ret1d"].std(ddof=0) + 1e-9)
    F["score_u"] = (-F["rank_adv"] / max(1, m)) * 2.0 + F["z_vol"] * 0.5 + F["z_ret"] * 0.5

    k = int(cfg.get("max_final_universe", 150))
    uni = F.sort_values("score_u", ascending=False).head(k)["ticker"].tolist()
    print(f"[DEBUG] final universe size: {len(uni)} sample: {uni[:10]}")
    return uni