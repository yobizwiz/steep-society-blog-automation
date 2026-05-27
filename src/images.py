"""Image generation + WebP optimization + Vision validation."""
from __future__ import annotations
import base64, io, json, re, time
import urllib.error, urllib.request
from utils import log

MAX_DIM = 1600
WEBP_QUALITY = 82

# Person/people-related words that trigger Imagen safety filter (returns empty predictions
# because of personGeneration: dont_allow). Replace with neutral object-focused phrasing.
PERSON_PATTERNS = [
    (r"\b(mom|mother|mother's|mommy|mum)\b", "tea gift"),
    (r"\b(dad|father|father's|daddy)\b", "tea gift"),
    (r"\b(woman|women|lady|ladies|girl|girls)\b", ""),
    (r"\b(man|men|guy|guys|boy|boys)\b", ""),
    (r"\b(person|people|someone|family|child|children|kid|kids|baby|babies)\b", ""),
    (r"\b(hand|hands|fingers|finger|arm|arms)\b", ""),
    (r"\b(ritual|enjoying|sipping|drinking|holding|pouring)\b", "still life of"),
    (r"\b(her|his|their|she|he|they)\b", "the"),
    (r"\bmorning routine\b", "morning still life"),
    (r"\bself-care\b", "tea setup"),
]


def sanitize_image_prompt(prompt):
    """Remove person references from prompt to avoid Imagen safety filter.
    Returns sanitized prompt; logs if changes were made."""
    original = prompt
    out = prompt
    for pat, repl in PERSON_PATTERNS:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    # collapse double spaces and stray punctuation
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\s*,\s*,+", ",", out)
    if out != original:
        log("  프롬프트 정화: 사람 관련 단어 제거됨")
    return out


def _clean_filename(name):
    name = re.sub(r"\.(jpg|jpeg|png|webp|gif|bmp)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    return name.strip("-")


class ImagenSafetyBlocked(RuntimeError):
    """Imagen returned empty predictions - prompt likely blocked by safety filter."""
    pass


def generate_imagen(prompt, *, api_key, model="imagen-4.0-generate-001",
                     n=1, aspect_ratio="16:9", max_retries=5):
    """Generate image via Imagen API with exponential backoff for 429 rate limits."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:predict?key={api_key}")
    body = json.dumps({
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": n, "aspectRatio": aspect_ratio,
                       "personGeneration": "dont_allow"},
    }).encode("utf-8")
    # Backoff seconds for attempts 1..5 (cumulative covers up to ~13 min)
    backoff_429 = [30, 90, 180, 360, 600]
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            out = []
            for pred in data.get("predictions", []):
                b64 = pred.get("bytesBase64Encoded") or pred.get("imageBytesBase64") or ""
                if b64:
                    out.append(base64.b64decode(b64))
            if out:
                return out
            raise ImagenSafetyBlocked(
                "empty predictions (safety filter blocked - prompt likely contains person/sensitive content)"
            )
        except ImagenSafetyBlocked:
            raise
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="ignore")[:300]
            last_err = f"HTTP {e.code}: {body_text}"
            if e.code in (400, 401, 403):
                raise RuntimeError(f"Imagen API {last_err}")
            if e.code == 429:
                if ("per_day" in body_text.lower() or "requests_per_day" in body_text.lower()
                        or "perday" in body_text.lower() or "PerDay" in body_text):
                    raise RuntimeError("DAILY_QUOTA_EXHAUSTED: " + body_text[:160])
                retry_after = 0
                try:
                    retry_after = int(e.headers.get("Retry-After", "0") or "0")
                except Exception:
                    pass
                wait = max(retry_after, backoff_429[min(attempt - 1, len(backoff_429) - 1)])
                log(f"  Imagen 429 (rate limit) 시도 {attempt}/{max_retries}, {wait}초 대기", "WARN")
                time.sleep(wait)
            else:
                wait = 5 * attempt
                log(f"  Imagen {e.code} 시도 {attempt}/{max_retries}, {wait}초 후 재시도", "WARN")
                time.sleep(wait)
        except Exception as e:
            last_err = str(e)
            wait = 5 * attempt
            log(f"  Imagen 예외 시도 {attempt}/{max_retries}: {e} ({wait}초 후 재시도)", "WARN")
            time.sleep(wait)
    raise RuntimeError(f"Imagen 재시도 모두 실패: {last_err}")


def optimize_to_webp(png_bytes, *, max_dim=MAX_DIM, quality=WEBP_QUALITY):
    try:
        from PIL import Image
    except ImportError:
        log("Pillow 없음", "WARN")
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > max_dim:
        scale = max_dim / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6)
    return buf.getvalue()


def verify_image_matches_prompt(image_bytes, prompt, *, anthropic_key,
                                  model="claude-haiku-4-5-20251001"):
    """Claude Vision으로 이미지가 프롬프트와 맞는지 검증.
    반환: (matches: bool, reason: str)"""
    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    user_text = (
        f"This image was generated by AI from this prompt:\n\n\"" + prompt[:500] + "\"\n\n"
        "Question: Does the image visually MATCH the prompt's described subject? "
        "If the prompt describes food/tea/scones but the image shows something completely "
        "unrelated (vehicles, animals, people, abstract shapes), it does NOT match.\n\n"
        "Reply with ONLY this JSON (no other text):\n"
        "{\"match\": true_or_false, \"reason\": \"brief explanation under 100 chars\"}"
    )
    body = json.dumps({
        "model": model, "max_tokens": 200, "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                    "media_type": "image/webp", "data": img_b64}},
                {"type": "text", "text": user_text},
            ],
        }],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log(f"  비전 검증 실패 (네트워크): {e} — 통과 처리", "WARN")
        return True, "verify_failed_pass"

    text = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")
    # Extract JSON
    m = re.search(r"\{[^{}]*\"match\"[^{}]*\}", text)
    if not m:
        log(f"  비전 응답 파싱 실패: {text[:100]} — 통과 처리", "WARN")
        return True, "parse_failed_pass"
    try:
        result = json.loads(m.group(0))
        return bool(result.get("match", True)), str(result.get("reason", ""))[:200]
    except Exception:
        return True, "parse_failed_pass"


def _fallback_prompt(filename_base):
    """If safety filter blocks even the sanitized prompt, build a generic
    object-only fallback from the filename (which is purely descriptive)."""
    keywords = re.sub(r"[-_]+", " ", _clean_filename(filename_base))
    return (
        f"Editorial still life photograph: {keywords}. "
        "No people, no human figures, no body parts. "
        "Soft natural daylight, cream linen background, "
        "shallow depth of field, magazine-quality composition, "
        "warm earthy tones."
    )



# === Steep Society 이미지 톤 (사용자 수동 사진 분위기에 맞춤) ===
# 실제 카메라로 찍은 에디토리얼 푸드 포토 느낌: 자연광, 차분한 어스/뉴트럴 톤,
# 사실적 질감(렌더/과채도/광택 X), 미니멀 탑다운 플랫레이, 진짜 소품, 여백.
STEEP_STYLE_SUFFIX = (
    " Shot as authentic editorial food photography on a real camera (35mm film look), "
    "soft natural window light with gentle realistic shadows, calm muted earthy and neutral "
    "color palette (slightly desaturated, true to life), photorealistic natural textures with "
    "subtle film grain, NObright glossy CGI render, no oversaturation, no harsh studio glare, "
    "thoughtfully composed with generous negative space, real tactile props "
    "such as natural linen, dried botanicals, bamboo, ceramic, on a light neutral stone/linen or "
    "warm rustic wood surface, often set within a real lived-in home scene (a kitchen dining table, a living-room side table, a sunlit windowsill) with softly blurred background and gentle bokeh for a sense of place, shallow depth of field — not always a tight flat macro close-up; understated cozy lifestyle mood, magazine-quality but unstaged and natural."
).replace("NObright","no bright")



def generate_gemini_image(prompt, *, api_key, model="gemini-3.1-flash-image-preview",
                          n=1, aspect_ratio="16:9", max_retries=5):
    """Generate image via Gemini multimodal model (:generateContent endpoint).
    Used for Nano Banana family (e.g., gemini-3.1-flash-image-preview)."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")
    full_prompt = (
        f"{prompt}\n\n"
        f"Output: a single high-quality {aspect_ratio} aspect ratio horizontal photograph."
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }).encode("utf-8")
    backoff_429 = [30, 90, 180, 360, 600]
    last_err = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            out = []
            for cand in data.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data") or {}
                    b64 = inline.get("data")
                    if b64:
                        out.append(base64.b64decode(b64))
            if out:
                return out
            raise ImagenSafetyBlocked("empty image output (safety filter or no image in response)")
        except ImagenSafetyBlocked:
            raise
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="ignore")[:300]
            last_err = f"HTTP {e.code}: {body_text}"
            if e.code in (400, 401, 403):
                raise RuntimeError(f"Gemini Image API {last_err}")
            if e.code == 429:
                if ("per_day" in body_text.lower() or "requests_per_day" in body_text.lower()
                        or "perday" in body_text.lower() or "PerDay" in body_text):
                    raise RuntimeError("DAILY_QUOTA_EXHAUSTED: " + body_text[:160])
                wait = backoff_429[min(attempt - 1, len(backoff_429) - 1)]
                log(f"  Gemini Image 429 시도 {attempt}/{max_retries}, {wait}초 대기", "WARN")
                time.sleep(wait)
            else:
                wait = 5 * attempt
                log(f"  Gemini Image {e.code} 시도 {attempt}/{max_retries}, {wait}초 후 재시도", "WARN")
                time.sleep(wait)
        except Exception as e:
            last_err = str(e)
            wait = 5 * attempt
            log(f"  Gemini Image 예외 시도 {attempt}/{max_retries}: {e}", "WARN")
            time.sleep(wait)
    raise RuntimeError(f"Gemini Image 재시도 모두 실패: {last_err}")


def generate_image_for_slot(*, prompt, filename_base, api_key, model,
                              variants=1, aspect_ratio="16:9",
                              anthropic_key=None, max_vision_retries=2):
    """이미지 생성 + 프롬프트 정화 + 비전 검증 + 불일치 시 자동 재생성."""
    clean_name = _clean_filename(filename_base)
    log(f"  이미지 생성 ({variants}장): {clean_name}")

    # Step 1: sanitize prompt to remove person references (Imagen safety filter)
    safe_prompt = sanitize_image_prompt(prompt) + STEEP_STYLE_SUFFIX

    last_webp = None
    last_png = None
    pngs = []

    # Force Nano Banana 2 (Gemini) for higher quality. Imagen kept as fallback.
    effective_model = "gemini-3.1-flash-image-preview"

    def _try_generate(p):
        if effective_model.startswith("gemini"):
            return generate_gemini_image(p, api_key=api_key, model=effective_model,
                                          n=variants, aspect_ratio=aspect_ratio)
        return generate_imagen(p, api_key=api_key, model=effective_model,
                                n=variants, aspect_ratio=aspect_ratio)

    for vision_try in range(max_vision_retries + 1):
        try:
            pngs = _try_generate(safe_prompt)
        except ImagenSafetyBlocked as e:
            log(f"  ⚠️ 안전 필터 차단됨 ({e}) — 폴백 프롬프트로 재시도", "WARN")
            fb = _fallback_prompt(filename_base)
            log(f"  폴백 프롬프트: {fb[:100]}...")
            try:
                pngs = _try_generate(fb)
            except ImagenSafetyBlocked:
                raise RuntimeError(
                    f"Imagen 안전 필터: 정화 프롬프트와 폴백 모두 차단됨 ({clean_name})"
                )
        if not pngs:
            raise RuntimeError(f"이미지 생성 실패: {safe_prompt[:60]}")
        best = max(pngs, key=len)
        webp = optimize_to_webp(best)
        last_webp = webp
        last_png = best

        if not anthropic_key:
            # 비전 키 없으면 검증 스킵
            log(f"  최적화 완료: {len(best):,}B → {len(webp):,}B ({len(webp)/len(best)*100:.0f}%)")
            break

        # 비전 검증
        log(f"  비전 검증 시도 {vision_try+1}/{max_vision_retries+1}")
        matches, reason = verify_image_matches_prompt(
            webp, safe_prompt, anthropic_key=anthropic_key)
        if matches:
            log(f"  ✅ 비전 검증 통과: {reason}")
            log(f"  최적화: {len(best):,}B → {len(webp):,}B ({len(webp)/len(best)*100:.0f}%)")
            break
        log(f"  ❌ 비전 검증 실패: {reason} — 재생성", "WARN")
        if vision_try == max_vision_retries:
            log(f"  비전 재시도 한도 도달 — 마지막 이미지 사용", "WARN")

    return {"webp_bytes": last_webp, "filename": f"{clean_name}.webp",
            "variants_count": len(pngs)}
