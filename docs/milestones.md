# 마일스톤 & Claude Code 세션 프롬프트

각 마일스톤 = Claude Code 세션 1~2회 분량. 완료 조건: 라운드트립 pytest 통과 + 사용자 GUI 확인.
아래 프롬프트는 세션 시작 시 그대로 붙여넣어 쓸 수 있는 초안이다.

---

## M0 — 스파이크: XDE 추출 실증 (반나절)

**목표:** pythonocc 로 NIST CTC 모델에서 매핑 문서 인벤토리 S1~S12 가 실제로 뽑히는지 검증.
매핑을 시작하기 전의 위험 제거 단계 — 특히 S7(GVP) 접근 가능 여부가 관건.

**프롬프트:**
> docs/mapping-draft.md 의 §1 인벤토리를 읽어라. tests/fixtures/ 의 STEP 파일 하나를 대상으로,
> S1~S12 각 항목을 추출해 JSON 으로 덤프하는 스파이크 스크립트 scripts/spike_extract.py 를 작성하라.
> 항목별로 성공/실패/부분성공을 표로 보고하고, 실패 항목은 원인과 대안을 제시하라.
> 특히 S7(GVP)이 XDE 로 접근되는지, 안 되면 BRepGProp 대체가 동작하는지 확인하라.
> 이 단계에서는 basyx 를 건드리지 마라.

**완료 판정:** S1~S6, S10~S12 성공 필수. S7 은 GVP 또는 GProp 중 하나로 값 확보.

---

## M1 — 단일 부품 → AASX (1주)

**목표:** 부품 1개짜리 STEP → TechnicalData(§3) + Models3D(§4, PreviewFile 은 placeholder 허용) 를
가진 AAS 1개를 .aasx 로 출력. 라운드트립 테스트 확립.

**프롬프트:**
> M0 스파이크 결과를 바탕으로 src/stp2aas/ 의 스텁을 구현하라. 스코프: 단일 부품(비어셈블리) STEP →
> 부품 AAS 1개(TechnicalData + Models3D 서브모델) → .aasx. 매핑 문서 §3(B1~B9), §4(C1~C12, C18~C24)를
> 따르고 각 매핑 코드에 항목 ID 주석을 달아라. PreviewFile 은 이번엔 단색 placeholder PNG 로.
> mapping/*.yaml 에서 semanticId 를 로드하는 구조를 만들고 하드코딩하지 마라.
> tests/test_roundtrip.py 를 완성해 basyx 재파싱 검증을 통과시켜라.

**완료 판정:** `python -m stp2aas fixture.stp -o out.aasx` 성공, pytest 통과, 사용자 GUI 확인.

---

## M2 — 어셈블리 → BOM + 다중 AAS (1~2주)

**목표:** 어셈블리 STEP → 어셈블리 AAS(02011 BOM, §2) + 부품별 AAS, SameAs 연결,
부품 STEP 분리 저장, 반복 부품 처리(D2).

**프롬프트:**
> 어셈블리 지원을 추가하라. 매핑 문서 §2(A1~A6)와 §0 의 AAS 구성 전략을 따르라.
> XDE label 트리 재귀 순회로 중간 표현(AssemblyNode/PartNode)을 만들고, 부품별 TopoDS_Shape 을
> 개별 .stp 로 내보내(STEPControl_Writer) supplementary file 로 임베드하라.
> 동일 shape 공유 부품은 AAS 를 하나만 만들고 여러 Node 의 SameAs 가 그것을 가리키게 하라(D2).
> 라운드트립 테스트에 BOM 트리 구조 검증(노드 수, HasPart 관계, SameAs 대상 존재)을 추가하라.

**완료 판정:** NIST CTC 어셈블리 모델 변환 성공, Package Explorer 에서 BOM 트리 탐색 가능.

---

## M3 — 미리보기 렌더 + 완성도 (1주)

**목표:** D6 실구현(오프스크린 렌더), CLI 옵션 정리, Nameplate 부분 매핑(§5) + 생략 정책(D7),
에러 처리, `--derive-lightweight` 예약 슬롯.

**프롬프트:**
> pythonocc 오프스크린 렌더러로 부품별 PreviewFile PNG(512²)를 생성하라. 헤드리스 환경에서
> OpenGL 실패 시 placeholder 로 자동 fallback 하고 경고 로그를 남겨라(D6).
> §5 Nameplate 부분 매핑과 D7 생략 정책을 구현하라. CLI 를 정리하고(--embed/--link-only,
> --derive-lightweight 는 NotImplementedError) README 사용법을 갱신하라.

---

## M4 — BaSyx 서버 데모 + 평가 데이터 (1주)

**목표:** 논문 평가 섹션 재료 생성.

**프롬프트:**
> scripts/demo_basyx.py 를 작성하라: 생성된 .aasx 를 로컬 BaSyx 서버(docker)에 업로드하고
> REST 로 BOM 서브모델을 조회해 트리를 출력하는 데모. 그리고 scripts/evaluate.py 로
> fixtures 전체에 대해 변환 커버리지(매핑 항목별 성공률), 파일 크기, 변환 시간을 CSV 로 산출하라.
> D2 의 Node-n개 vs BulkCount 크기 비교 실험도 포함하라.

---

## 세션 공통 규칙 리마인더

- 시작 시 CLAUDE.md 와 docs/mapping-draft.md 를 먼저 읽을 것
- ⚠️ semanticId 를 코드에 넣을 때 VERIFY 주석 + docs/verification-log.md 기록
- 마일스톤 범위를 넘는 리팩터링 금지, 완료 보고에 "GUI 확인 요청" 문구 포함
