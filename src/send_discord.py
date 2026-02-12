import os
import requests
from typing import Dict, List, Optional

MAX_TOTAL = 6000
MAX_TITLE = 256
MAX_DESC = 4096
MAX_FIELD_NAME = 256
MAX_FIELD_VAL = 1024


def _trim(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else (s[: max(0, n - 1)] + "…")


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
    return _trim("\n".join(lines), MAX_FIELD_VAL)


def _fmt_entry_signals(tech: Dict) -> str:
    """v2: 진입 타이밍 신호 표시"""
    lines = []

    # 눌림목 매수
    pullback = tech.get('pullback', {})
    if pullback.get('pullback_to_ma20'):
        lines.append("🎯 20일선 지지 반등 (눌림목 매수)")
    if pullback.get('pullback_to_ma50'):
        lines.append("🎯 50일선 지지 반등 (강한 지지)")
    if pullback.get('pullback_to_bb_lower'):
        lines.append("🎯 볼린저 하단 반등")

    # 돌파
    breakout = tech.get('breakout', {})
    if breakout.get('breakout_detected'):
        btype = breakout.get('breakout_type', '')
        if '20d' in btype:
            lines.append("🚀 20일 신고가 돌파 + 거래량 급증")
        else:
            lines.append("🚀 10일 고가 돌파 + 거래량 동반")

    # 다이버전스
    div = tech.get('divergence', {})
    if div.get('bullish_divergence'):
        lines.append("📊 RSI 강세 다이버전스 (반전 신호)")

    # 스토캐스틱
    if tech.get('stoch_oversold') and tech.get('stoch_cross_up'):
        lines.append("📈 스토캐스틱 과매도 반등")
    elif tech.get('stoch_cross_up'):
        lines.append("📈 스토캐스틱 골든크로스")

    # 볼린저 스퀴즈 + 돌파
    if tech.get('bb_squeeze') and breakout.get('breakout_detected'):
        lines.append("💥 볼린저 스퀴즈 후 돌파 (폭발적 움직임 예상)")

    if not lines:
        lines.append("⚡ 종합 기술적 지표 기반 추천")

    return "\n".join(lines)


def _fmt_risk_reward(tech: Dict) -> str:
    """v2: 손절가/목표가/R:R 비율"""
    rr = tech.get('risk_reward', {})
    stop = rr.get('stop_loss')
    target = rr.get('target_price')
    ratio = rr.get('risk_reward_ratio', 0)

    cur = tech.get('current_price', 0)

    lines = []
    if stop and target and cur > 0:
        stop_pct = (cur - stop) / cur * 100
        target_pct = (target - cur) / cur * 100
        lines.append(f"🛑 손절가: ${stop:.2f} (-{stop_pct:.1f}%)")
        lines.append(f"🎯 목표가: ${target:.2f} (+{target_pct:.1f}%)")
        
        if ratio >= 2.0:
            emoji = "✅"
        elif ratio >= 1.5:
            emoji = "⚠️"
        else:
            emoji = "🔴"
        lines.append(f"{emoji} R:R 비율: 1:{ratio:.1f}")
    else:
        lines.append("📊 R:R 계산 불가 (데이터 부족)")

    # 리스크 점수
    risk = tech.get('risk_score', 5)
    if risk <= 3:
        lines.append(f"🟢 리스크: 낮음 ({risk:.1f}/10)")
    elif risk <= 6:
        lines.append(f"🟡 리스크: 보통 ({risk:.1f}/10)")
    else:
        lines.append(f"🔴 리스크: 높음 ({risk:.1f}/10)")

    return "\n".join(lines)


def _fmt_technical_summary(tech: Dict, tech_score: float) -> str:
    """v2: 기술적 지표 요약 (간결)"""
    if not tech:
        return "기술적 분석 없음"

    lines = []

    # 이평선
    if tech.get('golden_cross'):
        lines.append("🟢 골든크로스")
    elif tech.get('dead_cross'):
        lines.append("🔴 데드크로스")
    if tech.get('ma_alignment'):
        lines.append("✅ 이평선 정배열")

    # MACD
    if tech.get('macd_cross_up'):
        lines.append("🟢 MACD 상향")
    elif tech.get('macd_cross_down'):
        lines.append("🔴 MACD 하향")

    # RSI & 스토캐스틱
    rsi = tech.get('rsi', 50)
    stoch_k = tech.get('stoch_k', 50)
    lines.append(f"📊 RSI {rsi:.0f} | Stoch %K {stoch_k:.0f}")

    # 거래량
    vol_r = tech.get('volume_ratio', 1.0)
    if tech.get('bullish_volume'):
        lines.append(f"💪 거래량 {vol_r:.1f}x (상승 동반)")
    elif vol_r > 1.5:
        lines.append(f"📊 거래량 {vol_r:.1f}x")

    # OBV / VWAP
    if tech.get('obv_rising'):
        lines.append("📈 OBV 상승 추세")
    vwap_r = tech.get('vwap_ratio', 1.0)
    if vwap_r != 1.0:
        lines.append(f"📊 VWAP 비율: {vwap_r:.3f}")

    # 추세 강도
    if tech.get('strong_trend'):
        lines.append(f"💎 강추세 ADX {tech.get('adx', 0):.0f}")

    # 확증 지표 수
    conf = tech.get('confirmation_count', 0)
    lines.append(f"⭐ 확증 {conf}개 | 점수 {tech_score:.1f}/10")

    return "\n".join(lines)


def _render_console(rows: List[Dict], label: str):
    print(f"\n=== {label} ===")
    if not rows:
        print("추천 없음")
        return
    for r in rows[:10]:
        reason = r.get("reason_obj", {}).get("reason", "")
        conf = r.get("reason_obj", {}).get("confidence", 0.0)
        caveat = r.get("reason_obj", {}).get("caveat", "투자 자문 아님")
        tech_score = r.get("tech_score", 0.0)
        tech = r.get("technical_analysis", {})
        print(f"- {r['ticker']} | Δ{r['day_ret']:.2f}% | Vol {r['vol_x']:.2f}x | "
              f"Tech {tech_score:.2f} | Total {r['score']:.2f}")
        if reason:
            print(f"  [AI] {_trim(reason, 160)} (conf {conf:.2f})")

        # 진입 신호
        entry = _fmt_entry_signals(tech)
        for line in entry.splitlines():
            print(f"  {line}")

        # 리스크/리워드
        rr = _fmt_risk_reward(tech)
        for line in rr.splitlines():
            print(f"  {line}")

        print(f"  [주의] {caveat}")
        print()


def _embed_from_row(r: Dict) -> Dict:
    reason = _trim(r.get("reason_obj", {}).get("reason", ""), 360)
    conf = r.get("reason_obj", {}).get("confidence", 0.0)
    caveat = r.get("reason_obj", {}).get("caveat", "투자 자문 아님")

    tech_score = r.get("tech_score", 0.0)
    tech = r.get("technical_analysis", {})

    title = _trim(f"🎯 {r['ticker']} · Score {r['score']:.2f}", MAX_TITLE)

    price_line = _fmt_price_line(r)
    desc = _trim(
        f"{price_line}\n"
        f"📊 수익률 {r['day_ret']:+.2f}% · 거래량 {r['vol_x']:.2f}x · 뉴스 {int(r['news_n'])}개",
        MAX_DESC
    )

    fields = [
        {
            "name": "🎯 진입 신호",
            "value": _trim(_fmt_entry_signals(tech), MAX_FIELD_VAL)
        },
        {
            "name": "📊 기술적 지표",
            "value": _trim(_fmt_technical_summary(tech, tech_score), MAX_FIELD_VAL)
        },
        {
            "name": "🛡️ 리스크/리워드",
            "value": _trim(_fmt_risk_reward(tech), MAX_FIELD_VAL)
        },
        {
            "name": "💡 AI 분석",
            "value": _trim(f"{reason}\n(confidence {conf:.2f})", MAX_FIELD_VAL)
        },
        {
            "name": "📰 뉴스",
            "value": _fmt_news_block(r.get("top_news", []), max_items=2, max_title=60)
        },
        {
            "name": "⚠️ 주의",
            "value": _trim(caveat, MAX_FIELD_VAL)
        },
    ]

    # 색상: R:R 비율에 따라
    rr = tech.get('risk_reward', {}).get('risk_reward_ratio', 0)
    if rr >= 2.5:
        color = 0x00ff00  # 초록
    elif rr >= 1.5:
        color = 0xffff00  # 노랑
    else:
        color = 0xff9900  # 주황

    return {"title": title, "description": desc, "fields": fields, "color": color}


def _calc_total_len(content: str, embeds: List[Dict]) -> int:
    total = len(content or "")
    for e in embeds:
        total += len(e.get("title", "")) + len(e.get("description", ""))
        for f in e.get("fields", []):
            total += len(f.get("name", "")) + len(f.get("value", ""))
        if "footer" in e and isinstance(e["footer"], dict):
            total += len(e["footer"].get("text", ""))
    return total


def _send_payload(url: str, content: str, embeds: List[Dict]):
    if url and "?wait=" not in url:
        url += "?wait=true"
    payload = {"content": content, "embeds": embeds}
    resp = requests.post(url, json=payload, timeout=20)
    print(f"[DEBUG] webhook status={resp.status_code}")
    if resp.status_code >= 400:
        print("[ERROR] webhook error:", resp.text[:500])
    return resp.status_code


def send_discord_with_reasons(rows: List[Dict], label: str = "US Stock Watchlist v2"):
    dry_run = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes", "on"}
    send_flag = os.environ.get("SEND_TO_DISCORD", "true").lower() not in {"0", "false", "no", "off"}
    url = (os.environ.get("DISCORD_WEBHOOK_URL", "") or "").strip().strip('"').strip("'")
    content = f"**{label}**\n🎯 진입 타이밍 중심 분석 | 과열 종목 자동 제외 | 손절·목표가 포함"

    print(f"[DEBUG] DRY_RUN={dry_run}, SEND_TO_DISCORD={send_flag}, URL_SET={bool(url)}")

    if dry_run or not send_flag or not url:
        _render_console(rows, label)
        return

    if not rows:
        _send_payload(url, content + "\n추천 없음 (과열 또는 적합 종목 부재)", [])
        return

    max_tickers = int(os.environ.get("MAX_TICKERS", "5"))
    rows = rows[:max_tickers]

    embeds = [_embed_from_row(r) for r in rows]

    batch, _ = [], len(content)
    for e in embeds:
        tentative = batch + [e]
        if _calc_total_len(content, tentative) > MAX_TOTAL:
            _send_payload(url, content, batch)
            batch = [e]
        else:
            batch = tentative

    if batch:
        _send_payload(url, content, batch)


# ══════════════════════════════════════════════════════
#  포지션 현황 Discord 전송
# ══════════════════════════════════════════════════════

def _pnl_emoji(pnl: Optional[float]) -> str:
    if pnl is None: return "⚪"
    if pnl >= 3:    return "🚀"
    if pnl > 0:     return "🟢"
    if pnl > -3:    return "🔴"
    return "💥"

def _status_label(status: str) -> str:
    return {
        "open":        "📂 보유중",
        "take_profit": "✅ 익절",
        "stop_loss":   "🛑 손절",
        "expired":     "⏰ 기간만료",
    }.get(status, status)

def _fmt_open_position(pos: Dict) -> str:
    """열린 포지션 한 줄 요약."""
    t        = pos["ticker"]
    entry    = pos["entry_price"]
    sl       = pos["stop_loss"]
    tp       = pos["take_profit"]
    cur      = pos.get("current_price")
    upnl     = pos.get("unrealized_pnl")
    days     = pos.get("days_held", "?")
    emoji    = _pnl_emoji(upnl)

    cur_str  = f"{cur:.2f}" if cur else "—"
    pnl_str  = f"{upnl:+.2f}%" if upnl is not None else "—"
    return (f"{emoji} **{t}** | 진입 {entry:.2f} → 현재 {cur_str} ({pnl_str})\n"
            f"   SL {sl:.2f} / TP {tp:.2f} | {days}일 경과")

def _fmt_closed_position(pos: Dict) -> str:
    """청산된 포지션 한 줄 요약."""
    t      = pos["ticker"]
    entry  = pos["entry_price"]
    exit_p = pos.get("exit_price", "—")
    pnl    = pos.get("pnl_pct")
    reason = _status_label(pos.get("status", ""))
    emoji  = _pnl_emoji(pnl)
    pnl_str = f"{pnl:+.2f}%" if pnl is not None else "—"
    return f"{emoji} **{t}** {reason} | {entry:.2f} → {exit_p} ({pnl_str})"

def _fmt_closed_detail(pos: Dict) -> str:
    """
    당일 청산 종목 상세 포맷.
    진입가 / 청산가 / 손익 / SL·TP / 보유일 / 청산 사유를 보여줌.
    """
    t       = pos["ticker"]
    entry   = pos["entry_price"]
    exit_p  = pos.get("exit_price")
    pnl     = pos.get("pnl_pct")
    status  = pos.get("status", "")
    sl      = pos.get("stop_loss")
    tp      = pos.get("take_profit")
    e_date  = pos.get("entry_date", "")
    x_date  = pos.get("exit_date", "")
    score   = pos.get("tech_score")

    # 보유 일수
    try:
        from datetime import datetime, timezone
        d0 = datetime.fromisoformat(e_date).replace(tzinfo=timezone.utc)
        d1 = datetime.fromisoformat(x_date).replace(tzinfo=timezone.utc)
        days_held = (d1 - d0).days
    except Exception:
        days_held = "?"

    pnl_str   = f"{pnl:+.2f}%" if pnl is not None else "—"
    exit_str  = f"{exit_p:.2f}" if exit_p is not None else "—"
    sl_str    = f"{sl:.2f}"   if sl  is not None else "—"
    tp_str    = f"{tp:.2f}"   if tp  is not None else "—"
    score_str = f"{score:.1f}" if score is not None else "—"

    status_map = {
        "take_profit": "✅ 익절",
        "stop_loss":   "🛑 손절",
        "expired":     "⏰ 기간만료",
    }
    reason_label = status_map.get(status, status)

    lines = [
        f"{_pnl_emoji(pnl)} **{t}**  {reason_label}  `{pnl_str}`",
        f"  진입 {entry:.2f} → 청산 {exit_str}  ({e_date} ~ {x_date}, {days_held}일)",
        f"  SL {sl_str} / TP {tp_str}  |  기술점수 {score_str}",
    ]
    return "\n".join(lines)


def _build_today_closed_embed(newly_closed: List[Dict]) -> Dict:
    """
    당일 청산 종목 전용 임베드.
    익절 / 손절 / 만료 그룹별로 묶어서 표시.
    """
    tp_list  = [p for p in newly_closed if p.get("status") == "take_profit"]
    sl_list  = [p for p in newly_closed if p.get("status") == "stop_loss"]
    exp_list = [p for p in newly_closed if p.get("status") == "expired"]

    # 당일 손익 합계 (동일 비중 가정 → 단순 평균)
    pnls = [p["pnl_pct"] for p in newly_closed if p.get("pnl_pct") is not None]
    avg_today = sum(pnls) / len(pnls) if pnls else 0.0
    day_emoji = "🟢" if avg_today >= 0 else "🔴"

    desc = (
        f"오늘 청산 {len(newly_closed)}건  |  "
        f"익절 ✅ {len(tp_list)} / 손절 🛑 {len(sl_list)} / 만료 ⏰ {len(exp_list)}\n"
        f"{day_emoji} 당일 평균 손익: **{avg_today:+.2f}%**"
    )

    fields = []

    if tp_list:
        fields.append({
            "name": f"✅ 익절 ({len(tp_list)}건)",
            "value": _trim("\n\n".join(_fmt_closed_detail(p) for p in tp_list), MAX_FIELD_VAL),
        })
    if sl_list:
        fields.append({
            "name": f"🛑 손절 ({len(sl_list)}건)",
            "value": _trim("\n\n".join(_fmt_closed_detail(p) for p in sl_list), MAX_FIELD_VAL),
        })
    if exp_list:
        fields.append({
            "name": f"⏰ 기간만료 ({len(exp_list)}건)",
            "value": _trim("\n\n".join(_fmt_closed_detail(p) for p in exp_list), MAX_FIELD_VAL),
        })

    # 색상: 익절 비율에 따라
    if len(newly_closed) > 0:
        tp_ratio = len(tp_list) / len(newly_closed)
        color = 0x00ff88 if tp_ratio >= 0.5 else 0xff4444
    else:
        color = 0x888888

    return {
        "title": f"🔔 당일 청산 리포트  ({len(newly_closed)}건)",
        "description": desc,
        "fields": fields,
        "color": color,
    }


def _build_position_embeds(summary: Dict) -> List[Dict]:
    """
    포지션 현황 임베드 생성.
    summary: position_tracker.get_summary() 결과
    """
    stats  = summary.get("stats", {})
    opens  = summary.get("open", [])
    recent = summary.get("recent_closed", [])

    # ── 누적 통계 ─────────────────────────────────
    total     = stats.get("total_trades", 0)
    wins      = stats.get("wins", 0)
    losses    = stats.get("losses", 0)
    exps      = stats.get("expired", 0)
    wr        = stats.get("win_rate", 0.0)
    avg       = stats.get("avg_pnl_pct", 0.0)
    total_pnl = stats.get("total_pnl_pct", 0.0)
    best      = stats.get("best_trade")  or {}
    worst     = stats.get("worst_trade") or {}

    stats_lines = [
        f"📈 총 거래: {total}회  |  🏆 승 {wins} / 패 {losses} / ⏰ 만료 {exps}",
        f"🎯 승률: {wr:.1f}%  |  평균 손익: {avg:+.2f}%  |  누적 손익: {total_pnl:+.2f}%",
    ]
    if best.get("ticker"):
        stats_lines.append(
            f"🥇 최고: {best['ticker']} {best.get('pnl_pct', 0):+.2f}%  "
            f"🥴 최저: {worst.get('ticker', '—')} {worst.get('pnl_pct', 0):+.2f}%"
        )

    # ── 열린 포지션 ───────────────────────────────
    if opens:
        for pos in opens:
            try:
                from datetime import datetime, timezone
                d0 = datetime.fromisoformat(pos.get("entry_date", "")).replace(tzinfo=timezone.utc)
                pos["days_held"] = (datetime.now(timezone.utc) - d0).days
            except Exception:
                pos["days_held"] = "?"
        open_lines = [_fmt_open_position(p) for p in opens]
    else:
        open_lines = ["현재 보유중인 포지션 없음"]

    # ── 최근 청산 이력 (당일 제외, 과거 5건) ───────
    if recent:
        closed_lines = [_fmt_closed_position(p) for p in recent]
    else:
        closed_lines = ["최근 청산 내역 없음"]

    fields = [
        {
            "name":  "📊 누적 성과",
            "value": _trim("\n".join(stats_lines), MAX_FIELD_VAL),
        },
        {
            "name":  f"📂 보유 포지션 ({len(opens)}개)",
            "value": _trim("\n".join(open_lines), MAX_FIELD_VAL),
        },
        {
            "name":  "🕘 최근 청산 이력",
            "value": _trim("\n".join(closed_lines), MAX_FIELD_VAL),
        },
    ]

    return [{"title": "📋 포지션 현황", "description": "", "fields": fields, "color": 0x00b4d8}]


def send_discord_position_report(summary: Dict, newly_closed: List[Dict]) -> None:
    """
    포지션 현황을 Discord로 전송.

    전송 순서:
      1. (당일 청산이 있을 때) 당일 청산 리포트 임베드  ← 신규
      2. 포지션 현황 임베드 (보유중 + 누적 통계)
    """
    dry_run   = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes", "on"}
    send_flag = os.environ.get("SEND_TO_DISCORD", "true").lower() not in {"0", "false", "no", "off"}
    url = (os.environ.get("DISCORD_WEBHOOK_URL", "") or "").strip().strip('"').strip("'")

    content = "**📋 포지션 현황 리포트**"

    # 임베드 조립: 당일 청산 먼저, 그다음 전체 현황
    embeds: List[Dict] = []
    if newly_closed:
        embeds.append(_build_today_closed_embed(newly_closed))
    embeds.extend(_build_position_embeds(summary))

    if dry_run or not send_flag or not url:
        print("\n" + content)
        for e in embeds:
            print(f"\n  ── {e.get('title', '')} ──")
            print(f"  {e.get('description', '')}")
            for f in e.get("fields", []):
                print(f"  [{f['name']}]")
                for line in f["value"].splitlines():
                    print(f"    {line}")
        return

    # 6000자 제한 고려 배치 전송
    batch: List[Dict] = []
    for e in embeds:
        tentative = batch + [e]
        if _calc_total_len(content, tentative) > MAX_TOTAL:
            _send_payload(url, content, batch)
            batch = [e]
        else:
            batch = tentative
    if batch:
        _send_payload(url, content, batch)


# ══════════════════════════════════════════════════════
#  (기존) 가격 포맷
# ══════════════════════════════════════════════════════

def _fmt_price_line(r: dict) -> str:
    p = r.get("last_price")
    pc = r.get("prev_close")
    if p is None and pc is None:
        return "💵 가격: —"
    if p is None and pc is not None:
        return f"💵 가격: — (전일 {pc:.2f})"
    if pc is None:
        return f"💵 가격: {p:.2f}"
    delta = ((p / pc) - 1) * 100 if pc else 0.0
    emoji = "🟢" if delta >= 0 else "🔴"
    return f"💵 가격: {p:.2f} (전일 {pc:.2f}, {emoji} {delta:+.2f}%)"
