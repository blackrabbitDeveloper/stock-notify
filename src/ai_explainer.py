import os
from typing import Dict, List

import google.generativeai as genai


SYSTEM_INSTRUCTION = (
    "You are a technical analysis expert and equity analyst.\n"
    "Respond in Korean.\n"
    "Analyze the given technical indicators, price/volume data, and recent news.\n"
    "Explain WHY this ticker is recommended for short-term trading in 2-3 sentences.\n"
    "Focus on: key technical signals (MA crossover, RSI, MACD, volume), momentum, and catalysts.\n"
    "Be specific but concise. Do not give financial advice."
)

def _mk_user_prompt(ticker: str, metrics: Dict, news: List[Dict], max_news:int=2, sum_len:int=120) -> str:
    """기술적 분석 정보를 포함한 프롬프트 생성"""
    tech = metrics.get('technical_signals', {})
    
    lines = [
        f"📊 티커: {ticker}",
        f"",
        f"💰 가격 및 거래량:",
        f"- 전일 수익률: {metrics.get('day_ret', 0):.2f}%",
        f"- 거래량 배수: {metrics.get('vol_x', 1):.2f}x (20일 평균 대비)",
    ]
    
    # 기술적 분석 정보 추가
    if tech:
        lines.append("")
        lines.append("📈 기술적 분석:")
        
        # 이동평균선
        if tech.get('golden_cross'):
            lines.append("- ✅ 골든크로스 발생 (5일선이 20일선 상향돌파)")
        elif tech.get('dead_cross'):
            lines.append("- ⚠️ 데드크로스 발생 (5일선이 20일선 하향돌파)")
        
        if tech.get('ma_alignment'):
            lines.append("- ✅ 이평선 정배열 (5일 > 10일 > 20일)")
        
        # RSI
        rsi = tech.get('rsi', 50)
        if tech.get('rsi_oversold'):
            lines.append(f"- RSI: {rsi:.1f} (과매도 구간, 반등 가능성)")
        elif tech.get('rsi_overbought'):
            lines.append(f"- RSI: {rsi:.1f} (과매수 구간, 조정 위험)")
        else:
            lines.append(f"- RSI: {rsi:.1f}")
        
        # MACD
        if tech.get('macd_cross_up'):
            lines.append("- ✅ MACD 상향돌파 (매수 신호)")
        elif tech.get('macd_cross_down'):
            lines.append("- ⚠️ MACD 하향돌파 (매도 신호)")
        
        macd_hist = tech.get('macd_histogram', 0)
        if macd_hist > 0:
            lines.append(f"- MACD 히스토그램: 양수 ({macd_hist:.3f})")
        
        # 볼린저 밴드
        bb_pos = tech.get('bb_position', 0.5)
        if bb_pos < 0.2:
            lines.append(f"- 볼린저밴드: 하단 근처 ({bb_pos*100:.0f}%, 반등 구간)")
        elif bb_pos > 0.8:
            lines.append(f"- 볼린저밴드: 상단 근처 ({bb_pos*100:.0f}%, 과열 구간)")
        
        # 거래량
        if tech.get('bullish_volume'):
            lines.append("- 💪 가격 상승 + 거래량 급증 (강한 매수세)")
        elif tech.get('volume_ratio', 1) > 2.0:
            lines.append(f"- 📊 거래량 급증 ({tech['volume_ratio']:.1f}배)")
        
        # 추세 강도
        if tech.get('strong_trend'):
            adx = tech.get('adx', 0)
            lines.append(f"- 💎 강한 추세 (ADX {adx:.1f})")
        
        # 기술적 점수
        tech_score = metrics.get('tech_score', 0)
        lines.append(f"")
        lines.append(f"⭐ 기술적 분석 점수: {tech_score:.2f}/10")
    
    # 뉴스 정보
    if news:
        lines.append("")
        lines.append("📰 최근 뉴스:")
        for i, n in enumerate(news[:max_news], 1):
            s = ((n.get("summary") or "")[:sum_len]).replace("\n"," ")
            lines.append(f"{i}. [{n.get('source','?')}, {n.get('hours_ago','?')}h] {n.get('title')}")
            if s:
                lines.append(f"   {s}")
    
    lines.append("")
    lines.append("위 기술적 분석과 뉴스를 종합하여, 단기 매매 관점에서 이 종목이 추천되는 핵심 이유를 한국어로 2-3문장으로 요약해주세요.")
    
    return "\n".join(lines)

def _extract_text(resp) -> str:
    """resp.text가 비어도 parts에서 텍스트를 합쳐 반환"""
    if getattr(resp, "text", None):
        return resp.text
    try:
        cand = resp.candidates[0]
        parts = getattr(cand, "content", {}).parts or []
        chunks = []
        for p in parts:
            if hasattr(p, "text") and p.text:
                chunks.append(p.text)
        return "\n".join(chunks).strip()
    except Exception:
        return ""

def explain_reason(ticker: str, metrics: Dict, news: List[Dict]) -> Dict:
    def _fallback(msg="fallback"):
        print(f"[DEBUG] explain_reason Fallback: {msg}")
        tech_score = metrics.get('tech_score', 0)
        day_ret = metrics.get('day_ret', 0)
        vol_x = metrics.get('vol_x', 1)
        
        tech = metrics.get('technical_signals', {})
        signals = []
        if tech.get('golden_cross'):
            signals.append("골든크로스")
        if tech.get('macd_cross_up'):
            signals.append("MACD 상향돌파")
        if tech.get('bullish_volume'):
            signals.append("거래량 동반 상승")
        
        if signals:
            reason = f"{', '.join(signals)} 신호 발생. 전일 {day_ret:.1f}% 상승, 거래량 {vol_x:.2f}배."
        else:
            reason = f"전일 {day_ret:.1f}% 상승, 거래량 {vol_x:.2f}배. 기술적 점수 {tech_score:.1f}점."
        
        return {
            "reason": reason,
            "confidence": 0.4,
            "caveat": "투자 자문 아님. 손절 필수."
        }
    
    GEMINI_MODEL = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    MAX_OUT = int(os.getenv("AI_EXPLAINER_MAX_TOKENS", "1024"))
    
    if not GOOGLE_API_KEY:
        return _fallback("no GOOGLE_API_KEY")

    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel(model_name=GEMINI_MODEL, system_instruction=SYSTEM_INSTRUCTION)

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

        # 1차 시도
        resp = _call(max_news=2, max_tokens=MAX_OUT)
        txt = _extract_text(resp)
        
        if getattr(resp, "candidates", None) and resp.candidates[0].finish_reason == "MAX_TOKENS":
            print("[DEBUG] Gemini finish_reason=MAX_TOKENS → retry with smaller prompt")
            resp = _call(max_news=1, max_tokens=MAX_OUT)
            txt = _extract_text(resp)

        if not txt:
            return _fallback("empty response")

        reason_text = txt.strip()
        if not reason_text:
            return _fallback("empty summary")
        
        print("[DEBUG] explain_reason OK via Gemini")
        return {
            "reason": reason_text, 
            "confidence": 0.65,  # 기술적 분석 포함으로 신뢰도 상승
            "caveat": "투자 자문 아님. 단기 매매 전략이므로 손절 필수."
        }

    except Exception as e:
        print(f"[ERROR] Gemini API error: {e}")
        return _fallback(repr(e))
