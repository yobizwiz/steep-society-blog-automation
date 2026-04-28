"""공용 유틸 — 환경변수 로딩, 경로, 로깅."""
from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "output"
LOGS_DIR = PROJECT_ROOT / "logs"


def load_env() -> dict:
    """api-keys.txt 로드."""
    env = {}
    path = PROJECT_ROOT / "api-keys.txt"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def load_yaml(path: Path) -> dict:
    """가벼운 YAML 로더 (의존성 없이 핵심 기능만)."""
    try:
        import yaml as _yaml  # type: ignore

        return _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        # fallback: 매우 단순한 YAML 파서 (key: value 와 들여쓰기 1단계만)
        return _simple_yaml_parse(path.read_text(encoding="utf-8"))


def _simple_yaml_parse(text: str) -> dict:
    """PyYAML 없을 때 간이 파서. schedule.yaml 구조에 맞춤."""
    result: dict = {}
    current_key = None
    current_dict: dict | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # 들여쓰기 없는 라인 = 새 항목
        if not raw.startswith(" ") and not raw.startswith("\t"):
            line = raw.rstrip()
            if line.endswith(":"):
                key = line[:-1].strip().strip('"').strip("'")
                current_key = key
                current_dict = {}
                result[key] = current_dict
            elif ":" in line:
                k, v = line.split(":", 1)
                result[k.strip()] = v.strip().strip('"').strip("'")
                current_key = None
                current_dict = None
        else:
            # 자식 항목
            if current_dict is None:
                continue
            line = raw.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                current_dict[k.strip()] = v.strip().strip('"').strip("'")
    return result


def load_few_shot_articles() -> list[dict]:
    path = CONFIG_DIR / "few_shot_articles.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_system_prompt() -> str:
    return (CONFIG_DIR / "system_prompt.md").read_text(encoding="utf-8")


def log(msg: str, level: str = "INFO") -> None:
    print(f"[{level}] {msg}", flush=True)


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
