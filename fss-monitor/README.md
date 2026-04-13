# ⚖ 금감원 제재사례 자동 모니터링

매일 오전 9시, 금감원 검사결과제재 게시판을 자동으로 확인하고
보험업 관련 제재사례를 네이버 메일로 알려주는 시스템입니다.

---

## 🚀 설치 방법 (5단계)

### 1단계. GitHub 저장소 만들기
1. [github.com](https://github.com) 로그인
2. 오른쪽 위 **`+`** → **`New repository`** 클릭
3. Repository name: `fss-monitor` 입력
4. **Private** 선택 (보안)
5. **Create repository** 클릭

### 2단계. 파일 업로드
아래 4개 파일을 저장소에 업로드하세요:
- `monitor.py`
- `requirements.txt`
- `.github/workflows/monitor.yml`

> 팁: GitHub 웹에서 **`Add file` → `Upload files`** 로 업로드 가능
> `.github/workflows/` 폴더는 직접 생성해야 합니다.

### 3단계. 네이버 메일 앱 비밀번호 발급
1. 네이버 로그인 → **내 정보** → **보안설정**
2. **2단계 인증** 활성화 (필수)
3. **애플리케이션 비밀번호** → 새 비밀번호 생성
4. 생성된 비밀번호 복사 (나중에 사용)

### 4단계. GitHub Secrets 등록
저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 이름 | 값 |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` (Anthropic 콘솔에서 발급) |
| `NAVER_EMAIL` | `yourname@naver.com` |
| `NAVER_PASSWORD` | 3단계에서 발급한 앱 비밀번호 |
| `RECIPIENT_EMAIL` | 알림 받을 이메일 (본인 또는 다른 주소) |
| `MY_PROFILE` | 아래 예시 참고 |

**MY_PROFILE 예시:**
```
보험업 종사자. 회사: OO생명보험. 업무: 개인보험 판매/모집 관리.
관심 키워드: 불완전판매, 허위고지, 모집질서, 보험금 지급 거절, 설계사 제재, 과태료
```

### 5단계. 수동 테스트 실행
저장소 → **Actions** → **금감원 제재사례 모니터링** → **Run workflow** → **Run workflow**

실행 로그를 확인하고 이메일이 오는지 체크하세요.

---

## ⏰ 실행 일정
- **매일 오전 09:00 (KST)** 자동 실행
- Actions 탭에서 수동으로도 실행 가능

## 📧 이메일 알림 기준
| 관련성 | 알림 여부 |
|---|---|
| 🔴 높음 | ✅ 포함 |
| 🟡 보통 | ✅ 포함 |
| 🟢 낮음 | ❌ 제외 |
| ⚪ 없음 | ❌ 제외 |

## 💰 비용
- GitHub Actions: **무료** (월 2,000분 무료, 하루 1분 사용)
- Anthropic API: 파일당 약 **$0.003** (월 수십 건 기준 $0.1 미만)

---

## 🔧 커스터마이징

`MY_PROFILE` Secret 값을 수정하면 관련성 판단 기준이 바뀝니다.

실행 시간 변경: `monitor.yml`의 `cron` 값 수정
- `'0 0 * * *'` = 오전 9시 (KST)
- `'0 1 * * *'` = 오전 10시 (KST)
