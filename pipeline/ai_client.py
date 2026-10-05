"""AI API 호출 모듈 (Gemini REST API 를 requests 로 직접 호출).

흐름: 프롬프트 구성 → HTTP POST → 응답 JSON 에서 텍스트 추출 → (실패 시) 재시도/대체 모델
- API 키는 환경변수(.env)에서만 읽고, URL 이 아닌 헤더로 전송한다 (로그에 키가 남지 않도록).
- 429(요청 한도 초과)·5xx·타임아웃은 잠시 기다렸다 재시도한다.
- 404(모델 없음), 429 중 '일일 한도 소진', 재시도 후에도 계속되는 5xx(서버 과부하)는 설정의 대체 모델로 넘어간다.
  (무료 요금제 한도는 모델별로 따로 적용되기 때문)
"""
import json
import logging
import re
import time

import requests

from pipeline.config import get_api_key

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class AIError(Exception):
    """AI 호출 실패 (재시도 후에도 실패)."""


class AIQuotaError(AIError):
    """모든 모델의 일일 사용 한도 소진 (오늘은 더 호출해도 실패)."""


class AIClient:
    def __init__(self, config: dict):
        ai = config.get("ai", {})
        self.provider = ai.get("provider", "gemini")
        if self.provider != "gemini":
            raise AIError(f"지원하지 않는 provider 입니다: {self.provider}")
        self.models = [ai.get("model", "gemini-2.5-flash")] + ai.get("fallback_models", [])
        self.base_url = ai.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        self.timeout = ai.get("timeout", 60)
        self.max_retries = ai.get("max_retries", 2)
        self.delay = ai.get("request_delay", 4.0)
        self.api_key = get_api_key(config)
        self._last_call = 0.0

    @property
    def model(self) -> str:
        return self.models[0]

    def _wait(self) -> None:
        """무료 요금제의 분당 요청 한도를 넘지 않도록 호출 간격을 둔다."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_call = time.monotonic()

    def generate(self, prompt: str, system: str | None = None,
                 json_mode: bool = False, max_tokens: int = 2048) -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        while self.models:
            try:
                return self._call(self.model, body)
            except _ModelNotFound:
                dead = self.models.pop(0)
                if not self.models:
                    raise AIError(f"사용 가능한 모델이 없습니다 (마지막 시도: {dead})")
                logger.warning("모델 %s 를 찾을 수 없어 %s 로 전환합니다.", dead, self.model)
            except _Overloaded as e:
                dead = self.models.pop(0)
                if not self.models:
                    raise AIError(str(e))
                logger.warning("모델 %s 가 계속 응답하지 않아(%s) %s 로 전환합니다.", dead, e, self.model)
            except _DailyQuota:
                dead = self.models.pop(0)
                if not self.models:
                    raise AIQuotaError(f"모든 모델의 일일 무료 한도를 다 썼습니다 (마지막 시도: {dead})")
                logger.warning("모델 %s 의 일일 한도가 소진되어 %s 로 전환합니다.", dead, self.model)
        raise AIError("사용 가능한 모델이 없습니다")

    def _call(self, model: str, body: dict) -> str:
        url = f"{self.base_url}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        attempts = self.max_retries + 1
        last_error = "알 수 없는 오류"
        server_busy = False

        for attempt in range(1, attempts + 1):
            self._wait()
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
            except requests.Timeout:
                last_error = f"타임아웃({self.timeout}초)"
            except requests.ConnectionError as e:
                last_error = f"연결 실패 ({type(e).__name__}: {str(e)[:120]})"
            else:
                if resp.status_code == 200:
                    return _extract_text(resp.json())
                if resp.status_code == 404:
                    raise _ModelNotFound()
                last_error = f"HTTP {resp.status_code}: {_error_message(resp)}"
                if resp.status_code == 429 and "PerDay" in resp.text:
                    raise _DailyQuota()  # 하루 한도는 기다려도 풀리지 않으므로 재시도하지 않음
                server_busy = resp.status_code >= 500
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = 10 * attempt  # 한도 초과/서버 오류는 점점 길게 기다린다
                    if attempt < attempts:
                        logger.warning("AI 호출 실패 (%s) [%d/%d], %d초 후 재시도",
                                       last_error, attempt, attempts, wait)
                        time.sleep(wait)
                    else:
                        logger.warning("AI 호출 실패 (%s) [%d/%d]", last_error, attempt, attempts)
                    continue
                raise AIError(last_error)  # 400/401/403 등은 재시도해도 같은 결과
            logger.warning("AI 호출 실패 (%s) [%d/%d]", last_error, attempt, attempts)
        if server_busy:  # 서버 과부하(5xx)는 모델별 문제일 수 있어 대체 모델을 시도
            raise _Overloaded(last_error)
        raise AIError(last_error)


class _ModelNotFound(Exception):
    pass


class _DailyQuota(Exception):
    pass


class _Overloaded(Exception):
    pass


def _error_message(resp: requests.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message", "")[:150]
    except ValueError:
        return resp.text[:150]


def _extract_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "응답 없음")
        raise AIError(f"AI 응답이 비어 있습니다 ({reason})")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise AIError(f"AI 응답에 텍스트가 없습니다 (finishReason={candidates[0].get('finishReason')})")
    return text.strip()


def parse_json(text: str) -> dict:
    """AI 가 돌려준 JSON 문자열을 dict 로 변환 (```json 코드블록이 섞여 와도 처리)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AIError(f"AI 응답을 JSON 으로 읽을 수 없습니다: {e}") from e
