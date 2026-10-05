"""설정 파일(config.json)과 환경변수(.env)를 읽어오는 모듈."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

VALID_DUPLICATE_POLICIES = ("skip", "upsert")


class ConfigError(Exception):
    """설정이 잘못되었을 때 발생하는 예외."""


def load_config(path: str = "config.json") -> dict:
    """config.json 을 읽어 dict 로 반환한다. .env 도 함께 로드한다."""
    load_dotenv()  # .env 파일의 값을 환경변수로 등록

    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {config_path}")

    try:
        with config_path.open(encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"설정 파일 형식이 잘못되었습니다: {e}") from e

    policy = config.get("duplicate_policy", "skip")
    if policy not in VALID_DUPLICATE_POLICIES:
        raise ConfigError(
            f"duplicate_policy 는 {VALID_DUPLICATE_POLICIES} 중 하나여야 합니다 (현재: {policy})"
        )
    return config


def get_api_key(config: dict) -> str:
    """AI API 키를 환경변수에서 가져온다. 키는 절대 코드/설정 파일에 직접 쓰지 않는다."""
    env_name = config.get("ai", {}).get("api_key_env", "GEMINI_API_KEY")
    key = os.getenv(env_name)
    if not key:
        raise ConfigError(
            f"환경변수 {env_name} 가 없습니다. .env.example 을 참고해 .env 파일을 만들어 주세요."
        )
    key = key.strip()
    if not key.isascii() or len(key) < 20:
        raise ConfigError(
            f"{env_name} 값이 올바른 API 키 형식이 아닙니다 (예시 문구가 그대로 있거나 일부만 복사됨). "
            ".env 파일을 확인해 주세요."
        )
    return key
