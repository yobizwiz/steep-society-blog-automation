# Steep Society 블로그 자동화

매일 1개 영어 블로그 글을 자동으로 생성·검증·게시하는 파이프라인.

- **콘텐츠**: Claude API 멀티패스 (초안 → 자기비판 → 수정 → 교차 검토)
- **이미지**: Imagen API (글 1편당 3~4장 생성, WebP 최적화 자동 적용)
- **게시**: Shopify Admin API (기본 초안 모드, 검토 후 발행)
- **검증**: H1 없음, 표 5행 이내, CTA 1개, 절대주소, °F/°C 병기 등 자동 체크
- **품질 가이드**: ChatGPT 프로젝트의 시스템 프롬프트 그대로 + few-shot 예시 3편

---

## 1. 최초 1회 설정

### 필요한 것
- Windows + Python 3.10 이상 ([설치 링크](https://www.python.org/downloads/))
  - 설치 시 **"Add Python to PATH"** 체크 잊지 말 것
- 발급된 API 키 3종 (이미 `api-keys.txt`에 저장됨)

### 의존성 설치
처음 한 번 실행하면 `run.bat` 또는 `test-dryrun.bat`가 자동으로 설치합니다. 수동 설치하려면:
```
pip install -r requirements.txt
```

---

## 2. 일일 운영 (매일 30초)

### A. 그날 글 자동 생성·게시 (추천)
```
run.bat
```
- `schedule.yaml`의 오늘 날짜 항목을 읽음
- 콘텐츠·이미지 생성 → 검증 → Shopify에 **초안**으로 게시
- 약 3~5분 소요
- 끝나면 Shopify 관리자에서 검토 후 "발행" 버튼

### B. 특정 날짜 글 생성
```
run.bat 2026-04-30
```

### C. 테스트 모드 (Shopify 게시 없이 로컬만)
```
test-dryrun.bat
test-dryrun.bat 2026-04-30
```
- `output/` 폴더에 글 JSON·이미지 저장
- 품질 미리 확인용

### D. 즉시 발행 (초안 거치지 않음)
`api-keys.txt`에서 `PUBLISH_MODE=publish`로 변경. 또는 한 번만 즉시 발행:
```
python src\main.py --date 2026-04-30 --publish-now
```

---

## 3. 결과물 위치

매 실행 후 `output/` 폴더에 생성:
- `2026-04-30-article.json` — 전체 글 데이터 (제목/본문/메타/이미지/판정 점수)
- `2026-04-30-featured-image-name.webp` — 대표 이미지
- `2026-04-30-body-image-name.webp` — 본문 이미지들
- `2026-04-30-report.md` — 한국어 리포트 (점수, 링크, 검증 결과)

---

## 4. 품질 검증

자동으로 실행되는 검증:
- H1 없음 / 표 5행 이내 / CTA 정확히 1개·Quick Recap 직하 / CTA 아래 본문 없음
- 절대주소 사용 / °F·°C 병기 / 이미지 개수 / 메타 길이 / slug 형식
- LLM 자기 비판 → 수정 (콘텐츠/SEO/전환 약점)
- 다른 모델로 교차 검토 (Sonnet 작성 → Opus 검토)
- 솔직한 점수 (콘텐츠/온페이지/전환 각 10/10 기준)

위반 발생 시 콘솔에 표시 + 리포트에 기록. 운영 중 점수가 낮으면 `config/system_prompt.md` 또는 `config/few_shot_articles.json`을 보강해 튜닝.

---

## 5. 스케줄 추가/수정

`config/schedule.yaml`에 매월 새 항목 추가:
```yaml
"2026-06-15":
  title: "Best Tea for Father's Day Gifts"
  type: longtail            # hub / longtail / quickfix / comparison / pairing
  cta_collection: tea_gift_sets_samplers  # collections.yaml의 키
```

CTA 컬렉션 추가는 `config/collections.yaml`에:
```yaml
new_collection_key:
  handle: collection-handle-on-shopify
  title: "Display Name"
  url: "https://steep-society.com/collections/collection-handle-on-shopify"
```

---

## 6. 시스템 프롬프트 변경

기존 ChatGPT 프로젝트 지침을 그대로 옮긴 `config/system_prompt.md`를 직접 편집하면 다음 실행부터 반영됩니다.

---

## 7. Few-shot 예시 추가

품질이 만족스럽지 않은 글이 나오면, 더 좋은 기존 글을 few-shot 예시로 보강:
```
python src\refresh_few_shots.py
```
(또는 `config/few_shot_articles.json`을 직접 편집)

---

## 8. 폴더 구조

```
2026-04-27-steep-society-blog-automation/
├── api-keys.txt                # API 키 + 설정 (절대 공유 금지)
├── run.bat                     # 매일 실행 (게시 모드)
├── test-dryrun.bat             # 테스트 (게시 없이 로컬만)
├── requirements.txt            # Python 의존성
├── README.md                   # 이 문서
├── config/
│   ├── system_prompt.md        # Steep Society 지침 (LLM 시스템 프롬프트)
│   ├── schedule.yaml           # 일별 발행 스케줄
│   ├── collections.yaml        # CTA 컬렉션 매핑
│   └── few_shot_articles.json  # 잘 나온 기존 글 3편 (자동 생성)
├── src/
│   ├── main.py                 # 오케스트레이터
│   ├── content.py              # Claude 멀티패스 글 생성
│   ├── images.py               # Imagen 이미지 생성 + WebP 최적화
│   ├── shopify_pub.py          # Shopify Admin API 게시
│   ├── validators.py           # 구조 검증
│   ├── utils.py                # 공용 유틸
│   ├── verify_keys.py          # API 키 동작 검증
│   └── get_shopify_token.py    # Shopify OAuth 토큰 재발급 (필요 시)
├── output/                     # 결과 저장소 (글 JSON, 이미지, 리포트)
└── logs/                       # 실행 로그
```

---

## 9. 비용 예상

매일 1편 운영 시 (월 30편 기준):
- Claude API: $1.5 ~ $4.5
- Imagen API: $3.6 (3장 × 30일)
- Shopify: $0
- **합계: 월 $5~8 (한화 약 7천~1만원)**

---

## 10. 문제 해결

### "ModuleNotFoundError" 에러
```
pip install -r requirements.txt
```

### "API 키 미입력" 에러
`api-keys.txt`에서 `PASTE_YOUR_..._HERE` 자리에 실제 키 입력 확인.

### Shopify 401 (Invalid API key)
Shopify 토큰이 만료/폐기됨. 재발급:
```
python src\get_shopify_token.py
```

### 검증에서 위반(violations) 발견
콘솔의 위반 목록 확인. 대부분 LLM이 1~2회 다시 시도하면 통과합니다. 반복적으로 위반 나면 `config/system_prompt.md`의 해당 룰을 더 강조.

### Imagen 호출 실패
Google Cloud 결제 활성화 안 됐을 가능성. https://console.cloud.google.com/billing

---

## 11. 검증 (작동 확인)

API 키 3종 정상 동작 확인:
```
python src\verify_keys.py
```

3개 모두 "성공"이면 운영 준비 완료.
