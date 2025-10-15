import os
import json
import re
from typing import Dict, List

import google.generativeai as genai


SYSTEM_INSTRUCTION = (
    "You are a concise equity analyst.\n"
    "Use price/volume context and up to 3 recent news to explain WHY this ticker is on a pre-open watchlist. "
    "Respond in Korean.\n"
    "Output STRICT JSON with keys:\n"
    "  reason (<= 2 sentences, summary),\n"
    "  bullets (array of up to 3 short evidence-based points),\n"
    "  confidence (0-1),\n"
    "  caveat (optional).\n"
    "Be specific (metrics, catalysts, risks) but do not give financial advice."
)

# ── 유틸: JSON 추출 보정 ────────────────────────────────────────────────────────
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)

def _safe_parse_json(text: str) -> Dict:
    """모델이 코드펜스/설명과 섞어서 줄 때 대비."""
    if not text:
        raise ValueError("empty content")
    # 1) 그대로 시도
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2) 첫 번째 {..} 블록을 찾아 파싱
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return json.loads(m.group(0))
    raise ValueError("no json block")

# ── 프롬프트 구성 ───────────────────────────────────────────────────────────────
def _mk_user_prompt(ticker: str, metrics: Dict, news: List[Dict], max_news:int=2, sum_len:int=120) -> str:
    lines = [
        f"티커: {ticker}",
        f"전일 수익률: {metrics.get('day_ret'):.2f}%",
        f"거래량/20일평균: x{metrics.get('vol_x'):.2f}",
        "최근 뉴스(최대 2건):"
    ]
    for i, n in enumerate(news[:max_news], 1):
        s = ((n.get("summary") or "")[:sum_len]).replace("\n"," ")
        lines.append(f"{i}. [{n.get('source','?')}, {n.get('hours_ago','?')}h] {n.get('title')}\n   - {s}")
    lines.append('JSON만 출력:\n{"reason":"...","bullets":["포인트1","포인트2"],"confidence":0.00,"caveat":"투자 자문 아님"}')
    return "\n".join(lines)

def _extract_text(resp) -> str:
    """resp.text가 비어도 parts에서 텍스트를 합쳐 반환"""
    if getattr(resp, "text", None):
        return resp.text
    # candidates -> content -> parts -> text
    try:
        cand = resp.candidates[0]
        parts = getattr(cand, "content", {}).parts or []
        chunks = []
        for p in parts:
            # text, functionCall 등 다양한 타입이 올 수 있음
            if hasattr(p, "text") and p.text:
                chunks.append(p.text)
        return "\n".join(chunks).strip()
    except Exception:
        return ""

def explain_reason(ticker: str, metrics: Dict, news: List[Dict]) -> Dict:
    def _fallback(msg="fallback"):
        print(f"[DEBUG] explain_reason Fallback: {msg}")
        return {
            "reason": f"전일 {metrics.get('day_ret'):.1f}% 및 거래량 x{metrics.get('vol_x'):.2f} 관측.",
            "bullets": [],
            "confidence": 0.4,
            "caveat": "투자 자문 아님"
        }
    # ── 환경설정 ───────────────────────────────────────────────────────────────────
    GEMINI_MODEL = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    MAX_OUT = int(os.getenv("AI_EXPLAINER_MAX_TOKENS", "1024"))
    TRANSPORT = os.getenv("GENAI_TRANSPORT", "rest")  # "rest" 권장
    
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
                    temperature=0.2,
                    max_output_tokens=MAX_OUT,
                    response_mime_type="application/json",
                ),
                request_options={"timeout": 300},  # 초
            )

        # 1차 시도
        resp = _call(max_news=2, max_tokens=MAX_OUT)
        txt = _extract_text(resp)
        if getattr(resp, "candidates", None) and resp.candidates[0].finish_reason == "MAX_TOKENS":
            print("[DEBUG] Gemini finish_reason=MAX_TOKENS → retry with smaller prompt")
            # 2차 축소 재시도: 뉴스 1건, 출력 256
            resp = _call(max_news=1, max_tokens=min(256, MAX_OUT))
            txt = _extract_text(resp)

        if not txt:
            return _fallback("empty response")

        data = _safe_parse_json(txt)
        data.setdefault("bullets", [])
        data.setdefault("caveat", "투자 자문 아님")
        if "reason" not in data: return _fallback("missing reason")
        if "confidence" not in data: data["confidence"] = 0.5
        print("[DEBUG] explain_reason OK via Gemini")
        return data

    except Exception as e:
        return _fallback(repr(e))