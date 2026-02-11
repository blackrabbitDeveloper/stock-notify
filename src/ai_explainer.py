import os
from typing import Dict, List

import google.generativeai as genai


SYSTEM_INSTRUCTION = (
    "You are a technical analysis expert specializing in entry-timing analysis.\n"
    "Respond in Korean.\n"
    "Analyze the technical indicators focusing on WHY NOW is a good entry point.\n"
    "Focus on:\n"
    "1. Entry timing signal (pullback to support, breakout, divergence, etc.)\n"
    "2. Risk/reward ratio and stop-loss level\n"
    "3. Volume confirmation\n"
    "4. Key risk factors to watch\n"
    "Be specific with numbers. 2-3 sentences max.\n"
    "Do NOT give financial advice. Always mention stop-loss."
)


def _mk_user_prompt(ticker: str, metrics: Dict, news: List[Dict],
                     max_news: int = 2, sum_len: int = 120) -> str:
    tech = metrics.get('technical_signals', {})

    lines = [
        f"Ticker: {ticker}",
        f"Day Return: {metrics.get('day_ret', 0):.2f}%",
        f"Volume Ratio: {metrics.get('vol_x', 1):.2f}x",
    ]

    if tech:
        # 진입 타이밍
        pullback = tech.get('pullback', {})
        if pullback.get('pullback_to_ma20'):
            lines.append("ENTRY: 20MA support bounce (pullback buy)")
        if pullback.get('pullback_to_ma50'):
            lines.append("ENTRY: 50MA support bounce (strong support)")
        if pullback.get('pullback_to_bb_lower'):
            lines.append("ENTRY: BB lower band bounce")

        breakout = tech.get('breakout', {})
        if breakout.get('breakout_detected'):
            lines.append(f"ENTRY: {breakout.get('breakout_type', '')} breakout with volume")

        div = tech.get('divergence', {})
        if div.get('bullish_divergence'):
            lines.append("ENTRY: RSI bullish divergence")

        # R:R
        rr = tech.get('risk_reward', {})
        if rr.get('stop_loss') and rr.get('target_price'):
            lines.append(f"Stop Loss: ${rr['stop_loss']:.2f}")
            lines.append(f"Target: ${rr['target_price']:.2f}")
            lines.append(f"R:R Ratio: 1:{rr.get('risk_reward_ratio', 0):.1f}")

        # 기본 지표
        rsi = tech.get('rsi', 50)
        stoch_k = tech.get('stoch_k', 50)
        lines.append(f"RSI: {rsi:.1f} | Stoch %K: {stoch_k:.1f}")

        if tech.get('golden_cross'):
            lines.append("Golden Cross detected")
        if tech.get('ma_alignment'):
            lines.append("MA Alignment (5>10>20)")
        if tech.get('macd_cross_up'):
            lines.append("MACD crossover UP")
        if tech.get('bullish_volume'):
            lines.append(f"Bullish volume ({tech.get('volume_ratio', 1):.1f}x)")
        if tech.get('obv_rising'):
            lines.append("OBV rising (buying pressure)")
        if tech.get('strong_trend'):
            lines.append(f"Strong trend (ADX {tech.get('adx', 0):.1f})")

        lines.append(f"Tech Score: {metrics.get('tech_score', 0):.2f}/10")

    if news:
        lines.append("Recent News:")
        for i, n in enumerate(news[:max_news], 1):
            lines.append(f"{i}. [{n.get('source', '?')}] {n.get('title', '')}")

    lines.append("")
    lines.append(
        "위 분석을 바탕으로, 이 종목의 '진입 타이밍'이 적절한 이유와 "
        "주요 리스크를 한국어 2-3문장으로 요약해주세요. 반드시 손절가를 언급하세요."
    )

    return "\n".join(lines)


def _extract_text(resp) -> str:
    if getattr(resp, "text", None):
        return resp.text
    try:
        parts = resp.candidates[0].content.parts or []
        return "\n".join(p.text for p in parts if hasattr(p, "text") and p.text).strip()
    except Exception:
        return ""


def explain_reason(ticker: str, metrics: Dict, news: List[Dict]) -> Dict:
    def _fallback(msg="fallback"):
        print(f"[DEBUG] explain_reason Fallback: {msg}")
        tech = metrics.get('technical_signals', {})
        signals = []

        pullback = tech.get('pullback', {})
        if pullback.get('pullback_to_ma20'):
            signals.append("20일선 지지 반등")
        if pullback.get('pullback_to_ma50'):
            signals.append("50일선 지지 반등")
        if tech.get('breakout', {}).get('breakout_detected'):
            signals.append("신고가 돌파")
        if tech.get('divergence', {}).get('bullish_divergence'):
            signals.append("RSI 강세 다이버전스")
        if tech.get('golden_cross'):
            signals.append("골든크로스")
        if tech.get('macd_cross_up'):
            signals.append("MACD 상향")

        rr = tech.get('risk_reward', {})
        rr_str = ""
        if rr.get('stop_loss'):
            rr_str = f" 손절 ${rr['stop_loss']:.2f}, 목표 ${rr.get('target_price', 0):.2f}."

        day_ret = metrics.get('day_ret', 0)
        vol_x = metrics.get('vol_x', 1)

        if signals:
            reason = f"{', '.join(signals)} 신호. {day_ret:.1f}% 변동, 거래량 {vol_x:.2f}배.{rr_str}"
        else:
            reason = f"기술적 점수 {metrics.get('tech_score', 0):.1f}점. {day_ret:.1f}% 변동.{rr_str}"

        return {"reason": reason, "confidence": 0.4, "caveat": "투자 자문 아님. 손절 필수."}

    GEMINI_MODEL = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    MAX_OUT = int(os.getenv("AI_EXPLAINER_MAX_TOKENS", "1024"))

    if not GOOGLE_API_KEY:
        return _fallback("no GOOGLE_API_KEY")

    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_INSTRUCTION
        )

        def _call(max_news=2, max_tokens=MAX_OUT):
            prompt = _mk_user_prompt(ticker, metrics, news, max_news=max_news, sum_len=120)
            return model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.15,
                    max_output_tokens=max_tokens,
                    response_mime_type="text/plain",
                ),
                request_options={"timeout": 300},
            )

        resp = _call(max_news=2, max_tokens=MAX_OUT)
        txt = _extract_text(resp)

        if getattr(resp, "candidates", None) and resp.candidates[0].finish_reason == "MAX_TOKENS":
            resp = _call(max_news=1, max_tokens=MAX_OUT)
            txt = _extract_text(resp)

        if not txt or not txt.strip():
            return _fallback("empty response")

        return {
            "reason": txt.strip(),
            "confidence": 0.65,
            "caveat": "투자 자문 아님. 단기 매매 전략이므로 손절 필수."
        }

    except Exception as e:
        print(f"[ERROR] Gemini API error: {e}")
        return _fallback(repr(e))
