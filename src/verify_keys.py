#!/usr/bin/env python3
"""API 키 3종 동작 검증 스크립트.

실행: python src/verify_keys.py
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict:
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip()
    return env


def mask(key: str) -> str:
    if not key or len(key) < 12:
        return "(미입력)"
    return f"{key[:8]}...{key[-4:]}"


def test_anthropic(key: str):
    if not key or "PASTE_YOUR" in key:
        return False, "키 미입력"
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(
            {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "hi"}],
            }
        ).encode("utf-8"),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:300]
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, f"네트워크/예외: {e}"


def test_google(key: str):
    if not key or "PASTE_YOUR" in key:
        return False, "키 미입력"
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read())
            count = len(data.get("models", []))
            has_imagen = any("imagen" in (m.get("name") or "").lower() for m in data.get("models", []))
            extra = " · Imagen 사용 가능" if has_imagen else " · Imagen 미노출(결제 필요할 수 있음)"
            return resp.status == 200, f"HTTP {resp.status}, 모델 {count}개{extra}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:300]
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, f"네트워크/예외: {e}"


def test_shopify(store_url: str, token: str):
    if not token or "PASTE_YOUR" in token:
        return False, "토큰 미입력"
    if not store_url:
        return False, "스토어 URL 미입력"
    url = f"https://{store_url}/admin/api/2024-04/shop.json"
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            shop = data.get("shop", {})
            return resp.status == 200, (
                f"HTTP {resp.status}, 스토어: {shop.get('name', '?')} ({shop.get('myshopify_domain', '?')})"
            )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:300]
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, f"네트워크/예외: {e}"


def main():
    project_root = Path(__file__).parent.parent
    env_path = project_root / "api-keys.txt"
    if not env_path.exists():
        print(f"[ERROR] api-keys.txt 파일 없음: {env_path}")
        sys.exit(1)
    env = load_env(env_path)

    print("=" * 64)
    print("Steep Society 블로그 자동화 — API 키 검증")
    print("=" * 64)

    print("\n[1/3] Anthropic Claude API")
    print(f"  키: {mask(env.get('ANTHROPIC_API_KEY', ''))}")
    ok1, msg1 = test_anthropic(env.get("ANTHROPIC_API_KEY", ""))
    print(f"  결과: {'성공' if ok1 else '실패'} — {msg1}")

    print("\n[2/3] Google AI Studio API")
    print(f"  키: {mask(env.get('GOOGLE_API_KEY', ''))}")
    ok2, msg2 = test_google(env.get("GOOGLE_API_KEY", ""))
    print(f"  결과: {'성공' if ok2 else '실패'} — {msg2}")

    print("\n[3/3] Shopify Admin API")
    print(f"  스토어: {env.get('SHOPIFY_STORE_URL', '?')}")
    print(f"  토큰: {mask(env.get('SHOPIFY_ADMIN_TOKEN', ''))}")
    ok3, msg3 = test_shopify(
        env.get("SHOPIFY_STORE_URL", ""),
        env.get("SHOPIFY_ADMIN_TOKEN", ""),
    )
    print(f"  결과: {'성공' if ok3 else '실패'} — {msg3}")

    print("\n" + "=" * 64)
    all_ok = ok1 and ok2 and ok3
    print(f"종합: {'전부 정상' if all_ok else '일부 실패 — 위 메시지 확인'}")
    print("=" * 64)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
