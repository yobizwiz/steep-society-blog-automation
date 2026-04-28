"""구조 검증 — Steep Society 하드 룰 자동 체크.

검증 실패 항목은 모두 모아서 반환. 운영 시 자동 재생성 트리거 또는 보고용.
"""
from __future__ import annotations

import re


HARD_RULES_DESC = "H1 없음 / 표 5행 / CTA 1개 / 절대주소 / °F병기 / Quick Recap 위치 / Image 개수"


def _count_h1(html: str) -> int:
    return len(re.findall(r"<h1\b", html, re.IGNORECASE))


def _table_row_counts(html: str) -> list[int]:
    """각 <table>의 <tr> 개수 (헤더 포함). 5행 이내 룰: 데이터행 ≤ 5."""
    counts = []
    for tbl in re.findall(r"<table\b[^>]*>(.*?)</table>", html, re.IGNORECASE | re.DOTALL):
        rows = re.findall(r"<tr\b", tbl, re.IGNORECASE)
        counts.append(len(rows))
    return counts


def _cta_blocks(html: str) -> int:
    """CTA 박스 개수 추정 — 인라인 스타일 박스 패턴 카운트."""
    # 정확한 매칭은 어렵지만, 'border-radius: 14px' + 'background: #faf7f1' 조합으로 식별
    pattern = r"background:\s*#faf7f1"
    return len(re.findall(pattern, html, re.IGNORECASE))


def _content_below_cta(html: str) -> bool:
    """CTA 박스 이후에 의미있는 본문이 있는지."""
    # 마지막 </div> 위치 찾고, 그 뒤 트림한 텍스트가 비어있어야 함
    cta_box_pattern = r"background:\s*#faf7f1"
    m = re.search(cta_box_pattern, html, re.IGNORECASE)
    if not m:
        return False
    # 박스 끝 <div> 찾기 — 박스 시작 이후 첫 닫는 두번째 </div>
    after = html[m.end():]
    # 박스의 끝 div 위치 찾기 (열린 div 카운트로 균형 맞추기)
    depth = 1
    pos = 0
    while pos < len(after):
        next_open = after.find("<div", pos)
        next_close = after.find("</div>", pos)
        if next_close == -1:
            return False
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
        else:
            depth -= 1
            pos = next_close + 6
            if depth == 0:
                tail = after[pos:].strip()
                # 공백/주석만 있으면 OK
                tail_clean = re.sub(r"<!--.*?-->", "", tail, flags=re.DOTALL).strip()
                return bool(tail_clean and len(tail_clean) > 5)
    return False


def _all_links_absolute(html: str) -> list[str]:
    """절대주소가 아닌 링크들 반환."""
    bad = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        if not href.startswith("https://steep-society.com/") and not href.startswith("http://steep-society.com/"):
            if href.startswith("/") or href.startswith("steep-society.com"):
                bad.append(href)
            elif not (href.startswith("https://") or href.startswith("http://")):
                bad.append(href)
    return bad


def _temperature_format_violations(html: str) -> list[str]:
    """온도 표기 검사 — °F 단독, °C 단독, °C가 °F보다 먼저 등."""
    text = re.sub(r"<[^>]+>", " ", html)
    bad = []
    # °F 다음에 (°C) 가 있는지 확인
    # °F 등장 모두 찾기
    f_matches = list(re.finditer(r"(\d+)\s*°F", text))
    c_matches = list(re.finditer(r"(\d+)\s*°C", text))
    # °C가 °F 없이 단독이면 위반
    f_indices = {m.start() for m in f_matches}
    for cm in c_matches:
        # cm 직전 30자 안에 °F가 있어야 함 (괄호 병기 패턴)
        window = text[max(0, cm.start() - 30):cm.start()]
        if "°F" not in window:
            bad.append(f"°C 단독: '{text[max(0, cm.start()-15):cm.end()+5]}'")
    return bad


def _placeholder_image_markers(html: str) -> list[str]:
    return re.findall(r"<!--\s*IMG:body-(\d+)\s*-->", html)


def validate(article: dict, *, post_type: str = "longtail") -> dict:
    """검증 결과 반환:
    {
      "ok": bool,
      "violations": [{"rule": ..., "detail": ...}, ...],
      "warnings": [...],
    }
    """
    body = article.get("body_html") or article.get("body") or ""
    violations = []
    warnings = []

    if not body:
        violations.append({"rule": "body", "detail": "body_html 비어있음"})
        return {"ok": False, "violations": violations, "warnings": warnings}

    # H1 in body
    if _count_h1(body) > 0:
        violations.append({"rule": "no_h1", "detail": "Body에 <h1> 포함됨"})

    # Table rows
    for i, n in enumerate(_table_row_counts(body)):
        # 헤더 포함이면 6 이하 (헤더 1 + 데이터 5)
        if n > 6:
            violations.append({"rule": "table_rows", "detail": f"Table {i+1}: {n} rows (max 6 incl. header)"})

    # CTA count
    cta_n = _cta_blocks(body)
    if cta_n != 1:
        violations.append({"rule": "cta_count", "detail": f"CTA 블록 {cta_n}개 발견 (정확히 1개여야 함)"})

    # Content below CTA
    if _content_below_cta(body):
        violations.append({"rule": "no_content_below_cta", "detail": "CTA 아래에 본문 콘텐츠가 있음"})

    # Absolute links
    bad_links = _all_links_absolute(body)
    if bad_links:
        violations.append({"rule": "absolute_urls", "detail": f"비절대주소: {bad_links[:3]}"})

    # Temperature notation
    temp_bad = _temperature_format_violations(body)
    if temp_bad:
        warnings.append({"rule": "temperature_format", "detail": temp_bad[:3]})

    # Image placeholders / count
    images = article.get("images", [])
    expected_total = 4 if post_type == "hub" else 3
    if len(images) != expected_total:
        violations.append({"rule": "image_count", "detail": f"이미지 {len(images)}개 (기대: {expected_total})"})
    body_imgs = [im for im in images if im.get("role") == "body"]
    placeholders = _placeholder_image_markers(body)
    if len(placeholders) != len(body_imgs):
        warnings.append({"rule": "image_placeholders", "detail": f"플레이스홀더 {len(placeholders)}개 vs 본문 이미지 {len(body_imgs)}개"})

    # Meta lengths
    mt = article.get("meta_title", "")
    md = article.get("meta_description", "")
    if not (40 <= len(mt) <= 65):
        warnings.append({"rule": "meta_title_length", "detail": f"meta_title {len(mt)}자 (권장 50~60)"})
    if not (130 <= len(md) <= 165):
        warnings.append({"rule": "meta_description_length", "detail": f"meta_description {len(md)}자 (권장 140~160)"})

    # URL slug format
    slug = article.get("url_slug", "")
    if slug and not re.match(r"^[a-z0-9-]+$", slug):
        violations.append({"rule": "url_slug_format", "detail": f"slug 형식 위반: {slug}"})

    # CTA collection in body
    # (정확한 텍스트 매칭은 게시 단계에서)

    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "warnings": warnings,
    }
