import os
import requests
from typing import Dict, List

MAX_TOTAL = 6000
MAX_TITLE = 256
MAX_DESC = 4096
MAX_FIELD_NAME = 256
MAX_FIELD_VAL = 1024

def _trim(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else (s[: max(0, n-1)] + "…")

def _fmt_news_block(top_news: List[Dict], max_items: int = 2, max_title: int = 70) -> str:
    if not top_news:
        return "최근 핵심 뉴스 없음"
    lines = []
    for n in top_news[:max_items]:
        title = _trim(n.get("title") or "", max_title)
        src = n.get("source") or "src"
        h = n.get("hours_ago", "?")
        url = (n.get("url") or "").strip()
        if url:
            lines.append(f"- [{src}] {title} ({h}h) <{url}>")
        else:
            lines.append(f"- [{src}] {title} ({h}h)")
    txt = "\n".join(lines)
    return _trim(txt, MAX_FIELD_VAL)

def _fmt_technical_signals_with_score(tech: Dict, tech_score: float) -> str:
    """
    기술적 분석 신호 + 점수 계산 내역
    각 신호가 얼마나 기여했는지 명확하게 표시
    """
    if not tech:
        return "기술적 분석 없음"
    
    lines = []
    breakdown = []  # 점수 분해
    
    # 1. 골든/데드 크로스
    if tech.get('golden_cross'):
        lines.append("🟢 골든크로스 (5일선↑20일선)")
        breakdown.append("  +2.5  골든크로스")
    elif tech.get('dead_cross'):
        lines.append("🔴 데드크로스 (5일선↓20일선)")
        breakdown.append("  -1.5  데드크로스")
    
    # 2. 이평선 정배열
    if tech.get('ma_alignment'):
        lines.append("✅ 이평선 정배열 (5>10>20)")
        breakdown.append("  +1.5  이평선 정배열")
    
    # 3. MACD
    if tech.get('macd_cross_up'):
        lines.append("🟢 MACD 상향돌파")
        breakdown.append("  +1.8  MACD 상향돌파")
    elif tech.get('macd_cross_down'):
        lines.append("🔴 MACD 하향돌파")
        breakdown.append("  -1.0  MACD 하향돌파")
    
    if tech.get('macd_histogram', 0) > 0:
        lines.append(f"📈 MACD 히스토그램 양수")
        breakdown.append("  +0.5  MACD 히스토그램 양수")
    
    # 4. 거래량
    vol_ratio = tech.get('volume_ratio', 1.0)
    if tech.get('bullish_volume'):
        lines.append(f"💪 가격↑ + 거래량↑ ({vol_ratio:.1f}배)")
        breakdown.append("  +2.0  거래량 동반 상승")
    elif vol_ratio > 2.0:
        lines.append(f"📊 거래량 급증 ({vol_ratio:.1f}배)")
        breakdown.append("  +1.0  거래량 급증")
    
    # 5. RSI
    rsi = tech.get('rsi', 50)
    if 30 < rsi < 50:
        lines.append(f"📊 RSI {rsi:.1f} (과매도 탈출)")
        breakdown.append("  +1.2  RSI 과매도 탈출")
    elif tech.get('rsi_overbought'):
        lines.append(f"⚠️ RSI {rsi:.1f} (과매수)")
        breakdown.append("  -0.8  RSI 과매수")
    else:
        lines.append(f"📊 RSI {rsi:.1f}")
    
    # 6. 볼린저 밴드
    bb_pos = tech.get('bb_position', 0.5)
    if 0.1 < bb_pos < 0.3:
        lines.append(f"📉 볼린저밴드 하단 반등 ({bb_pos*100:.0f}%)")
        breakdown.append("  +1.0  BB 하단 반등")
    elif bb_pos > 0.9:
        lines.append(f"📈 볼린저밴드 상단 ({bb_pos*100:.0f}%)")
        breakdown.append("  -0.5  BB 상단 과열")
    
    # 7. 추세 강도
    if tech.get('strong_trend'):
        adx = tech.get('adx', 0)
        lines.append(f"💎 강한 추세 (ADX {adx:.1f})")
        breakdown.append("  +0.8  강한 추세")
    
    # 8. 이평선 괴리율
    ma5_dev = tech.get('ma5_deviation', 0)
    if -3 < ma5_dev < 5:
        breakdown.append("  +0.5  적정 이평선 괴리")
    elif ma5_dev > 10:
        breakdown.append("  -0.5  과도한 상승")
    
    # 구분선 및 점수 분해
    if lines:
        lines.append("")
        lines.append("─" * 32)
        lines.append("⭐ 점수 계산 내역:")
        lines.extend(breakdown)
        lines.append("─" * 32)
        lines.append(f"📊 총점: {tech_score:.1f} / 10.0")
    
    return "\n".join(lines) if lines else "신호 없음"

def _render_console(rows: List[Dict], label: str):
    print(f"\n=== {label} ===")
    if not rows:
        print("추천 없음"); return
    for r in rows[:10]:
        reason = r.get("reason_obj", {}).get("reason", "")
        conf = r.get("reason_obj", {}).get("confidence", 0.0)
        caveat = r.get("reason_obj", {}).get("caveat", "투자 자문 아님")
        tech_score = r.get("tech_score", 0.0)
        print(f"- {r['ticker']} | Δ {r['day_ret']:.2f}% | Vol x{r['vol_x']:.2f} | "
              f"Tech {tech_score:.2f} | News {int(r['news_n'])} | Total {r['score']:.2f}")
        if reason: print(f"  [AI] {_trim(reason,160)} (conf {conf:.2f})")
        price_line = _fmt_price_line(r)
        print(f"  {price_line}")
        
        # 기술적 신호 + 점수 내역 출력
        tech_signals = _fmt_technical_signals_with_score(
            r.get("technical_analysis", {}), 
            tech_score
        )
        for line in tech_signals.splitlines():
            print(f"  {line}")
        
        print()  # 종목 간 구분
        for line in _fmt_news_block(r.get("top_news", [])).splitlines():
            print(f"  {line}")
        print(f"  [주의] {caveat}")

def _embed_from_row(r: Dict) -> Dict:
    reason = _trim(r.get("reason_obj", {}).get("reason", ""), 360)
    conf = r.get("reason_obj", {}).get("confidence", 0.0)
    caveat = r.get("reason_obj", {}).get("caveat", "투자 자문 아님")
    
    tech_score = r.get("tech_score", 0.0)
    title = _trim(f"🎯 {r['ticker']} · Score {r['score']:.2f} (Tech {tech_score:.2f})", MAX_TITLE)

    price_line = _fmt_price_line(r)
    desc = _trim(
        f"{price_line}\n"
        f"📊 수익률 {r['day_ret']:+.2f}% · 거래량 {r['vol_x']:.2f}x · 뉴스 {int(r['news_n'])}개 (+{r['news_bonus']:.2f})",
        MAX_DESC
    )
    
    # 기술적 신호 + 점수 내역
    tech_with_score = _fmt_technical_signals_with_score(
        r.get("technical_analysis", {}),
        tech_score
    )
    
    fields = [
        {
            "name": "📈 기술적 분석 (신호 + 점수)", 
            "value": _trim(tech_with_score, MAX_FIELD_VAL)
        },
        {
            "name": "💡 AI 추천 사유", 
            "value": _trim(f"{reason}\n(confidence {conf:.2f})", MAX_FIELD_VAL)
        },
        {
            "name": "📰 뉴스 하이라이트", 
            "value": _fmt_news_block(r.get("top_news", []), max_items=2, max_title=60)
        },
        {
            "name": "⚠️ 주의사항", 
            "value": _trim(caveat, MAX_FIELD_VAL)
        },
    ]
    
    return {"title": title, "description": desc, "fields": fields, "color": 0x00ff00}

def _calc_total_len(content: str, embeds: List[Dict]) -> int:
    total = len(content or "")
    for e in embeds:
        total += len(e.get("title","")) + len(e.get("description",""))
        for f in e.get("fields", []):
            total += len(f.get("name","")) + len(f.get("value",""))
        if "footer" in e and isinstance(e["footer"], dict):
            total += len(e["footer"].get("text",""))
        if "author" in e and isinstance(e["author"], dict):
            total += len(e["author"].get("name",""))
    return total

def _send_payload(url: str, content: str, embeds: List[Dict]):
    # wait=true로 상태코드/본문 확인
    if url and "?wait=" not in url:
        url += "?wait=true"
    payload = {"content": content, "embeds": embeds}
    resp = requests.post(url, json=payload, timeout=20)
    print(f"[DEBUG] webhook status={resp.status_code} len={len(resp.text or '')}")
    if resp.status_code >= 400:
        print("[ERROR] webhook error body:", resp.text[:500])
    return resp.status_code

def send_discord_with_reasons(rows: List[Dict], label: str = "US Pre-Open Watchlist"):
    dry_run = os.environ.get("DRY_RUN","").lower() in {"1","true","yes","on"}
    send_flag = os.environ.get("SEND_TO_DISCORD","true").lower() not in {"0","false","no","off"}
    url = (os.environ.get("DISCORD_WEBHOOK_URL","") or "").strip().strip('"').strip("'")
    content = f"**{label}**\n📊 기술적 분석 기반 단기 매매 추천 (2단계 필터링)"

    print(f"[DEBUG] DRY_RUN={dry_run}, SEND_TO_DISCORD={send_flag}, URL_SET={bool(url)}")

    if dry_run or not send_flag or not url:
        _render_console(rows, label); return

    if not rows:
        _send_payload(url, content + "\n추천 없음", []); return

    max_tickers = int(os.environ.get("MAX_TICKERS", "5"))
    rows = rows[:max_tickers]

    # 2) 임베드 생성
    embeds = [_embed_from_row(r) for r in rows]

    # 3) 배치 전송(6000자 넘지 않도록)
    batch, acc_len = [], len(content)
    for e in embeds:
        tentative = batch + [e]
        L = _calc_total_len(content, tentative)
        if L > MAX_TOTAL:  # 배치 전송하고 새 배치 시작
            _send_payload(url, content, batch)
            batch = [e]
        else:
            batch = tentative

    if batch:
        _send_payload(url, content, batch)


def _fmt_price_line(r: dict) -> str:
    p = r.get("last_price")
    pc = r.get("prev_close")
    if p is None and pc is None:
        return "💵 가격: —"
    if p is None and pc is not None:
        return f"💵 가격: — (전일 {pc:.2f})"
    if pc is None:
        return f"💵 가격: {p:.2f}"
    delta = ((p/pc)-1)*100 if pc else 0.0
    emoji = "🟢" if delta >= 0 else "🔴"
    return f"💵 가격: {p:.2f} (전일 {pc:.2f}, {emoji} {delta:+.2f}%)"
