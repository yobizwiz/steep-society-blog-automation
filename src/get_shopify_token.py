#!/usr/bin/env python3
"""Shopify Admin API access token 자동 발급 (OAuth 플로우).

사용법:
  python src/get_shopify_token.py

api-keys.txt의 SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET, SHOPIFY_STORE_URL을
읽어서 OAuth 인증 URL을 생성합니다. 사용자가 브라우저에서 한 번 승인하고
리디렉션된 URL을 다시 입력하면, 그 안의 인증 코드를 access token으로 교환
하고 api-keys.txt의 SHOPIFY_ADMIN_TOKEN= 줄을 자동으로 갱신합니다.
"""
from __future__ import annotations

import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SCOPES = "write_content,read_content,write_files,read_files,read_products"
REDIRECT_URI = "https://steep-society.com"


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


def update_env_file(path: Path, key: str, new_value: str) -> None:
    """api-keys.txt에서 KEY=... 줄을 안전하게 교체하거나 없으면 추가."""
    lines = []
    found = False
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(f"{key}=") and not stripped.startswith("#"):
                lines.append(f"{key}={new_value}\n")
                found = True
            else:
                lines.append(line)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={new_value}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main() -> None:
    project_root = Path(__file__).parent.parent
    env_path = project_root / "api-keys.txt"
    if not env_path.exists():
        print(f"[ERROR] api-keys.txt 파일 없음: {env_path}")
        sys.exit(1)

    env = load_env(env_path)
    client_id = env.get("SHOPIFY_CLIENT_ID", "").strip()
    client_secret = env.get("SHOPIFY_CLIENT_SECRET", "").strip()
    shop = env.get("SHOPIFY_STORE_URL", "").strip()

    missing = []
    if not client_id or "PASTE_YOUR" in client_id:
        missing.append("SHOPIFY_CLIENT_ID")
    if not client_secret or "PASTE_YOUR" in client_secret:
        missing.append("SHOPIFY_CLIENT_SECRET")
    if not shop:
        missing.append("SHOPIFY_STORE_URL")
    if missing:
        print(f"[ERROR] api-keys.txt에 다음 값 입력 필요: {', '.join(missing)}")
        sys.exit(1)

    state = secrets.token_hex(16)
    auth_url = (
        f"https://{shop}/admin/oauth/authorize?"
        f"client_id={client_id}&"
        f"scope={SCOPES}&"
        f"redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}&"
        f"state={state}"
    )

    print("=" * 64)
    print("Shopify OAuth — Admin API 토큰 발급")
    print("=" * 64)
    print()
    print("[1단계] 아래 URL을 브라우저에 붙여넣고 방문하세요.")
    print("        (Shopify 관리자에 미리 로그인되어 있어야 합니다.)")
    print()
    print(auth_url)
    print()
    print("[2단계] 권한 승인 화면이 뜨면 '앱 설치' 또는 'Update app' 클릭")
    print()
    print("[3단계] 승인 후 페이지가 steep-society.com 으로 리디렉션됩니다.")
    print("        브라우저 주소창에 표시되는 전체 URL을 통째로 복사하세요.")
    print("        (예: https://steep-society.com/?code=XXX&shop=YYY...)")
    print()
    redirect_url = input("[4단계] 복사한 URL을 여기에 붙여넣고 Enter: ").strip()

    if not redirect_url:
        print("[ERROR] URL이 비어있습니다.")
        sys.exit(1)

    parsed = urllib.parse.urlparse(redirect_url)
    params = urllib.parse.parse_qs(parsed.query)
    code = params.get("code", [None])[0]
    received_state = params.get("state", [None])[0]
    received_shop = params.get("shop", [None])[0]

    if not code:
        print("\n[ERROR] URL에서 'code=' 파라미터를 찾지 못했습니다.")
        print("        URL 전체를 정확히 복사했는지 확인하세요.")
        sys.exit(1)
    if received_state and received_state != state:
        print(f"\n[경고] state 불일치 — 그래도 진행합니다.")
    if received_shop and received_shop != shop:
        print(f"\n[경고] shop 불일치 (api-keys.txt: {shop}, 응답: {received_shop})")

    print("\n토큰 교환 중...")

    exchange_url = f"https://{shop}/admin/oauth/access_token"
    body = json.dumps(
        {"client_id": client_id, "client_secret": client_secret, "code": code}
    ).encode("utf-8")
    req = urllib.request.Request(
        exchange_url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")[:500]
        print(f"\n[실패] HTTP {e.code}: {body_text}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[실패] {e}")
        sys.exit(1)

    token = data.get("access_token", "")
    if not token:
        print(f"\n[실패] 응답에 access_token 없음. 응답: {data}")
        sys.exit(1)

    print(f"\n[성공] Access token 발급 완료: {token[:8]}...{token[-4:]}")
    update_env_file(env_path, "SHOPIFY_ADMIN_TOKEN", token)
    print("       api-keys.txt의 SHOPIFY_ADMIN_TOKEN= 자동 교체 완료")
    print()
    print("다음 단계: python src/verify_keys.py 실행 — 3개 모두 통과해야 정상")


if __name__ == "__main__":
    main()
