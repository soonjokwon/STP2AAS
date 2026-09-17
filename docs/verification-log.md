# semanticId 검증 로그

## 2026-09-07 후속 구현·검증

2026-09-07 구현 상태. 아래는 그 시점의 검토 기록이다.

- optional SameAs 제거, Entity.globalAssetId 조회, typed HasPart 참조로 정리.
- XML dependency-v2 및 경로 namespace, 물성 합계 누락 정책, custom ConversionProvenance 구현.
- centroid idShort 교정과 C22 topology별 5종 representation/optional Geometry 정책 반영.
- 실제 sdist→wheel→외부 경로 import 검증, YAML 4개 bytes 일치, SDK store 호환.
- 전체 **74 tests passed**, deprecation을 오류로 검사; **Ruff 전체 통과**.
- 독립 validator의 잘못된 reference key type 통과와 손상된 provenance 보고 누락도
  별도 검토에서 재현해 수정하고 negative control을 추가.
- AS1+Fusion 7개 입력×3모드, 공식 pinned AAS 3.1 XSD와 별도 XML profile 검증.
  최신 보고서: `out/followup-2026-09-07/validation-results.json`.
- 8,251건 전체 기록은 보존·구분. 최신 패키지에 대한 외부 consumer 및 다중 exporter
  정확도 실험은 수행한 것으로 주장하지 않음.
- 원고·그림1~6 SVG/PDF·표·참고문헌을 보완. 그림7은 historical GUI capture로 명시.
- XML v2의 ID 재변환 및 외부 YAML의 RepresentationKinds 추가 정책은
  `identity-policy.md`와 `mapping-draft.md`에 기록.

## 2026-09-06~07 통합 검토 및 보완

기존 기록의 체크 표시는 해당 시점의 일부 검사 결과이며 표준 전체 적합성 인증이 아니다.

- 변경 전 pytest 17개 통과. 신규 회귀 4개는 수정 전 모두 실패하여 결함 재현.
  수정 및 XML 정상 재사용·깊이 경계 보강 후 **24 passed, 32 deprecation warnings**.
- XML 단일 부품을 STEP으로 잘못 첨부하는 문제, 서브어셈블리 prototype scene에
  상위 occurrence 배치가 포함되는 문제, 파일 내부 cycle과 빈 XML 오류를 수정.
- 새 XML 파일 내부 깊이 한계: Part 경로 20개 허용 / 21개 명시적 ValueError.
- 실제 AS1 신규 변환 및 SDK 재읽기: 9 AAS / 22 submodel / 22 supplementary.
  출력 `out/review-2026-09-06/as1-reviewed.aasx`.
- `scripts/audit_evidence.py`로 과거 CSV 8,251개 고유 ID와 합계 재집계.
  AAS 58,653 / submodel 126,977 / occurrence 69,415 일치. 이번 전체 코퍼스 재실행 아님.
- 과거 ‘17.4 CPU-hours’는 perf_counter로 합산한 worker elapsed hours로 정정.
  SDK read는 측정 구간 밖이며, 61분 wall clock은 CSV로 검증되지 않음.
- AS1 직렬화 ‘10 kB 미만’은 ZIP 압축 크기. XML 원 크기는 169~269 kB.
  부속 파일 표에 기존 누락된 JSON 4/1/1개를 추가.
- **미해결 P1:** 02011-1-1 p.17의 SameAs 대상은 Entity지만 코드가 asset external
  reference를 생성한다. semanticId 문자열 일치와 관계 의미 적합성은 별개다.
- 현재 S8 MaterialTool은 비활성이고 S7은 computed-only. 기존 ‘재질 추출 경로’와
  GVP 지원 서술은 구현 현황으로 제한. XML dependency hash 및 부분 물성 합계는 후속 과제.
- Ruff 전체 기존 35건 유지; 새 테스트·집계 스크립트 Ruff 통과.
  Package Explorer 수동 GUI 검사와 실시간 BaSyx 실험은 이번에 수행하지 않음.
- 다음 우선순위: SameAs Entity 대상 설계 및 독립 검증 → dependency/revision identity
  정책 → 형식·exporter별 ground truth 기반 평가 → wheel YAML/환경 재현성 점검.

이후의 2026-08 기록은 과거 이력으로 보존하며, 위의 정정과 다르면 최신 검토를 따른다.

## semanticId 상태

대상은 매핑 문서가 고정한 스펙 버전이다. 이후 메이저 버전(02003 v2, 02006 v3)은
IRDI/IRI 가 바뀌므로 자동 이전하지 않는다.

| 항목 ID | semanticId | 상태 | 근거 (스펙 문서/페이지) | 일자 |
|---|---|---|---|---|
| C1~C25 (02026-1-0) | https://admin-shell.io/idta/Models3D/... | ✅ 검증 | IDTA 02026-1-0 스펙 원문. C1 MIME `application/step` 은 관행(공식 `model/step` 미등록). C25 는 D8 로 미방출 | 2026-07-23 / 2026-08-18 |
| A1~A6 (02011-1-1) | https://admin-shell.io/idta/HierarchicalStructures/... | ✅ 검증 | IDTA-02011-1-1 PDF (2024-06). Submodel `/1/1/Submodel`, 요소는 `/1/0` | 2026-08-18 |
| B0~B2 (02003-1-2) | ZVEI/TechnicalData/... + ECLASS IRDI #002/#001 | ✅ 검증 | IDTA-02003-1-2 PDF (2022-10). v2.0 은 `0173-1#01-AHX837#002` 등으로 이전 — 핀 유지 | 2026-08-18 |
| B3~B7 props | https://step2aas.org/props/... | 자체 정의 | 표준 어휘 부재(갭 §3). 논문에서 논의 | 2026-07-23 |
| D (02006-2-0) | https://admin-shell.io/zvei/nameplate/2/0/Nameplate + IRDI | ✅ 검증 | IDTA-02006-2-0 PDF. 기본 정책상 서브모델 생략(D7). v3.0 IRI 는 `idta/nameplate/3/0` | 2026-08-18 |

⚠️ 로 남은 것은 C1 MIME 선택뿐. 코드의 `# VERIFY` 주석은 위 표가 ✅ 인 항목에서 제거함.

## M0 스파이크 결과 (XDE 추출 인벤토리 S1~S14)

`extract/xde.py` 로 합성 fixture 및 실제 AP242 STEP(NX/Datakit) 부품에서 검증:

| 항목 | 상태 | 비고 |
|---|---|---|
| S1 제품 계층 (NAUO 트리) | ✅ | `GetFreeShapes` + `GetComponents` 재귀 (STP2X3D 구조 이식) |
| S2 인스턴스 변환행렬 | ✅ | `GetLocation().Transformation()` → 4×4. 02011 미매핑, D8 PlacementScene |
| S3 부품명 | ✅ | `TDF_Label.GetLabelName()` (pythonocc 편의 메서드) |
| S4 부품 식별자 | ⚠️ 부분 | STEP `product.id` 는 XDE 로 별도 노출 안 됨 → label entry 대체(갭 G2) |
| S5 반복 부품 수량 | ✅ | 동일 referred label(ref_key) 공유 카운트, D2. BulkCount 는 evaluate.py 만 |
| S6 형상 (B-rep) | ✅ | `GetShape` |
| S7 GVP (부피/표면적/중심) | ⚠️→✅ | GVP 는 XDE 로 접근 불가 → `BRepGProp` 계산, provenance=`computed` (B3~B5) |
| S8 재질 | ✅ 경로 | `SetMatMode` + `MaterialTool`. 파일에 재질이 없으면 공란 (B6/B7 생략) |
| S10 단위계 | ✅ | OCCT STEP 리더가 mm 로 정규화 |
| S11 헤더 메타 | ✅ | `extract/p21_header.py` 텍스트 파싱, FILE_SCHEMA→AP203/214/242 |
| S12 바운딩 박스 | ✅ | `Bnd_Box` |
| S14 PMI 존재 | ✅ | `DimTolTool.GetDimension/GeomToleranceLabels` 존재 검사 |

검증 데이터: 합성 fixture(single_part/assembly) + NIST/Datakit AP242 실모델.

## AP242 Domain Model XML (BOM) 입력 — 구현 완료

`extract/ap242xml.py` (사용자 요청으로 §6a future work 에서 승격). ISO 10303-4442 ed-3
(CAx-IF/MBx-IF Assembly Structure) XML 을 동일 PartNode IR 로 파싱하여 기존 매퍼/writer 재사용.

| 데이터셋 | 형식 | 결과 |
|---|---|---|
| km3 EROD-SUSPENSION (Datakit) | all-in-one (트리 1파일 + 외부 .stp) | 101 노드 / 73 occ / 58 unique, 변환·라운드트립 ✅ |
| r50j Torque Convertor (allinone) | all-in-one | 91 노드 / 87 occ / 26 unique, 형상 전부 해석 ✅ |
| r50j Torque Convertor (nested) | 다중 .stpx (서브어셈블리 재귀) | allinone 과 **동일 트리** 재구성 ✅ (재귀·D2 검증) |

파일명 해석은 `ExternalItem/Id`, `FileLocationIdentification/SourceId`, `Id/Identifier@id`
세 방언을 모두 지원. 누락 형상 파일은 gap 로그 후 geometry-less 노드로 처리.

## 2026-08-19 측정 (논문 Table 5 / §6 재측정)

**AS1 세 구성 모드 실측** — 입력은 `out/as1-oc-214-brep.aasx` 에서 추출한 원본 AS1
(`tests/as1/as1_root.stp`; Datakit/2008 AP214 헤더 보존 확인). 재생성 명령:
`python -m step2aas -i tests/as1/as1_root.stp -o out/as1_table5_<mode>.aasx --assembly-structure <mode>`

| 모드 | AAS | 서브모델 | BOM | ArcheType | supplementary | 크기 |
|---|---|---|---|---|---|---|
| hierarchical | 9 | 22 | 4 | OneDown | 9 stp + 9 png | 0.539 MB |
| flat | 6 | 13 | 1 | Full | 6 stp + 6 png | 0.325 MB |
| single | 1 | 3 | 1 | Full | 6 stp + 6 png | 0.322 MB |

구조 수치는 논문 초안과 일치. **크기는 초안(0.22/0.14/0.13 MB)과 달라 갱신함** — 프리뷰
PNG 가 hierarchical 패키지의 45%(236 KB)를 차지하며, 초안 수치는 placeholder 프리뷰
시절 측정치로 추정. AAS 직렬화 자체는 모든 모드에서 10 KB 미만.

**모드 간 불변식 (논문 §4) 실측 확인:** 루트 EntryNode `globalAssetId` 3모드 공통
(`urn:step2aas:asset:38023b91-…`), 루트 AAS id 는 모드별 상이, 부품 AAS 5개는
hierarchical/flat 간 공유(6개 중 5개 겹침). 전용 회귀 테스트
`test_composition_modes_hold_section4_invariants` 추가 (`tests/test_roundtrip.py`).

**Fusion 360 Gallery 코퍼스 — 전량 스윕 완료 (8,251 / 8,251)**

`python scripts/run_fusion_experiment.py tests/fusion360 16 6`
(다운로드 → 청크 추출 → `exp_fusion_full.py` 스윕까지 일괄. 산출: `out/fusion_full.csv`)

| 항목 | 실측값 | 논문 초안값 |
|---|---|---|
| 변환·라운드트립 | **8,251 / 8,251 성공, 실패 0** | 8,251/8,251 ✅ |
| AAS | **58,653** | 58,538 (0.2% 차) |
| 서브모델 | **126,977** | 126,714 (0.2% 차) |
| occurrence | 총 69,415 · 평균 8.4 · 중앙값 2 · p90 21 · p99 102 · **최대 409** | 최대 409 ✅ |
| 고유 부품 | 48,982 | — |
| 입력 STEP | 최대 150.6 MB | — |
| 패키지 | 평균 1.67 MB · **최대 186.9 MB** | 184 MB (근사) |
| 프로토타입 재사용 | **14.2%** (1,173/8,251), 최대 **120×** | 14%, 120× ✅ |
| 단일 occurrence 모델 | 3,714 (45%) | — |
| 소요 | CPU 17.4 h · 평균 7.6 s/어셈블리 · 61분 (16워커) | "2시간 미만" ✅ |

초안 수치가 0.2% 오차 내로 재현되었다. 이전 실행이 실제로 있었고 CSV 만 유실된 것으로
판단됨(미세 차이는 변환기 버전 차이). 논문 §6.1 을 실측값으로 갱신함. 초안이 서술한
"실패 3건(99.96%)" 중 진짜 결함(154자 부품명 → idShort 128자 제약 위반)은 코드에
반영돼 있고, 이번 클린 스윕에서는 실패 0건이므로 서술을 그에 맞게 고쳤다.

추출: 아카이브당 약 193초, 청크당 약 2.2 GB (assembly.step + assembly.json 선택 추출).

⚠️ 다운로드 스크립트 결함 기록: S3 가 연결을 조기에 닫으면 `resp.read()` 가 예외 없이
빈 값을 반환해 **잘린 파일이 완료로 오인**되었다(아카이브 01: 1.80/2.21 GB, 07: 1.23/2.09 GB).
`download_fusion.py` 가 전송 후 Content-Length 를 대조해 `ShortRead` 를 던지고 이어받도록
수정. 또한 한국어 cp949 콘솔에서 em-dash/§ 가 `UnicodeEncodeError` 를 유발하므로
스크립트 로그를 ASCII 로 정리하고 stdout 재설정 헬퍼를 추가함.

## 2026-08-18 보완

- 중간 서브어셈블리 BOM Node 를 Co-managed 로 바꿔 SameAs 가 없는 AAS 를 가리키지 않게 함
- `lxml` 을 environment.yml / pyproject 에 명시, mapping YAML 탐색 경로 보강
- S8 MaterialTool, D5 FileVersionId = 파일 해시 접두, D7 provenance 를 02026 fallback 상수에도 부여
- D8: C25 미방출, PlacementScene 을 G1 공식 우회책으로 확정
- 라운드트립 pytest 복구 (`tests/`), `scripts/evaluate.py` / `scripts/demo_basyx.py`


## 2026-09-09 — 제공 파일에 맞춘 C21 PMI 정책

원본 XDE의 `PartNode.has_pmi`를 분할·재생성 STEP의 `EmbeddedInfo="PMI"`로
그대로 전파하던 모순을 수정했다. 새 XCAF 문서를 만드는 형상 exporter는 형상·색상을
복사하지만 GD&T label/graphical annotation을 복사하지 않는다.

- whole-model P21 및 XML의 외부 part STEP을 원본 바이트 그대로 제공하거나 원본 링크로
  참조할 때만 해당 원본의 탐지 근거를 C21에 전파한다.
- 재생성 leaf/sub-assembly/XML root 또는 형상 파일 없음은 C21 PMI를 생략한다.
- 원본 탐지 플래그는 유지한다. whole-model root는 하위 노드의 탐지 근거를 포함한다.
- 직접 Models3D mapper를 부를 때도 제공 파일에 대한 명시적 근거가 기본적으로 필요하다.
- 신규 `tests/test_pmi_payload.py` 10건으로 수정 전 오류를 재현하고 수정 후 통과했다.
  전체 `pytest tests -q` 84건, 변경 코드 Ruff 통과.
- 테스트의 PMI flag는 합성 주입이다. 파일 계보와 메타데이터 정책을 검사하며,
  실제 semantic/graphical PMI 정확도·검출률·적합성을 측정한 결과는 아니다.
- 2026-09-07 실험 24개 AASX를 읽기 전용 검사한 결과 기존 C21 PMI 항목은 0개였다.
  기존 실험을 재실행한 것으로 서술하지 않는다.

증거: `out/lipman-layout-2026-09-09/pmi-code-verification.json` 및 `pmi-code.diff`
(로컬 `out/` 산출물; 이 저장소에는 커밋하지 않음).
