# STEP → AAS 매핑 초안 (v0.3)

**프로젝트:** STEP to Twin — STEP 모델로부터 AAS 디지털 트윈 생성
**개정일:** 2026-08-18 (v0.3: AP242 XML, semanticId 스펙 대조, `--assembly-structure` 구성 모드, downstream 시나리오)
**상태:** 구현 명세 — 대상 스펙 버전은 02011-1-1, 02003-1-2, 02006-2-0, 02026-1-0 (신규 메이저 버전으로의 이전은 별도 결정)

> **2026-09-07 구현 보완:** A4는 self-managed Entity의 `globalAssetId`를 통한
> 자산 조회로 정의한다. 선택 항목인 `SameAs`는 생략하며 `HasPart` 양끝은 같은 BOM의
> Entity ModelReference다. D3은 해당 속성이 모든 하위 occurrence에서 있을 때만 합산한다.
> D5의 XML v2는 선택된 구조의 의존 파일 manifest와 상대 경로 namespace를 사용한다.
> 출처·도구 버전·mode·추출 범위는 root Models3D의 custom `ConversionProvenance`로 제공한다.
> 세부 migration은 `identity-policy.md`, 검증 기록은 `verification-log.md` 참조.
> S7은 현재 geometry 계산, S8 재질 추출은 Windows native crash 회피로 비활성 상태다.
> XML 내부 Part 경로는 20개까지이며 순환·깊이 초과는 입력 오류다.

이 문서는 세 가지 용도를 겸한다: (1) 변환기 구현 명세, (2) 논문 Table 2(축약 매핑)와
Table 3(갭 분석)의 기초 자료, (3) 매핑 불가 항목의 갭 분석과 **downstream 활용
시나리오**(논문 Introduction / Discussion 재료).

---

## 0. 전제: AAS 구성 전략

| 결정 사항 | 채택안 | 근거 |
|---|---|---|
| AAS 단위 | **부품(part)별 AAS 1개 + 어셈블리 AAS** (기본 hierarchical; 아래 구성 모드 참조) | AAS 철학상 자산 단위가 부품. 부품 재사용/공급망 시나리오에 자연스러움 |
| 어셈블리 구조 표현 | 어셈블리 AAS의 02011 서브모델이 EntryNode가 되고, Node의 globalAssetId가 자식 자산을 식별. **자체 AAS가 없는** 중간 서브어셈블리만 Co-managed (flat 모드) | 02011-1-1 Self- vs Co-managed |
| AAS id 생성 | `urn:step2aas:{uuid5}` — 파일 해시 + prototype `ref_key` 기반 결정론적 UUID | 재변환 시 동일 id 보장 (idempotency) |
| 구성 모드 (`--assembly-structure`) | **hierarchical**(기본): 고유 부품·고유 서브어셈블리마다 AAS, 각 어셈블리 BOM은 직계 자식만(A6 ArcheType=`OneDown`), Node의 globalAssetId로 자식 자산 조회. **flat**: 루트 AAS 1개 + 고유 부품 AAS, BOM은 전체 트리 중첩(ArcheType=`Full`), 자체 AAS가 없는 중간 서브어셈블리 Node는 co-managed. **single**: 전체 모델이 AAS 1개, 모든 부품은 Model3D 엔트리, BOM 전체 트리는 co-managed(ArcheType=`Full`) | 사용처별 트레이드오프(표준 충실 vs 패키지 단순성) |
| 모드 간 id 규칙 | 루트 **globalAssetId는 세 모드 공통**(동일 물리 자산), 루트 **AAS/서브모델 id는 모드별 상이**. 부품 AAS id는 hierarchical/flat 간 공유(내용 동일, D2) | 같은 파일의 여러 모드 산출물을 한 저장소에 동시 적재 가능 |
| 형상 파일 배치 | **원본 STEP만** 부품 단위로 분리하여 AASX supplementary file 로 임베드. 경량 포맷(X3D/glTF) 파생은 future work — CLI 에 `--derive-lightweight` 슬롯만 예약 | 02026 취지가 "형상 재정의가 아닌 제공(provision)". 원본만으로 템플릿 요구 충족 |
| 추출 엔진 | **pythonocc-core (XDE/XCAF) 단독** + P21 헤더 텍스트 파싱. XDE 순회 로직은 STP2X3D(C++)의 구조를 이식 | 단일 언어 저장소, 미리보기 렌더까지 동일 스택에서 해결 |
| 대상 스키마 | AP203/AP214/AP242 (P21) **및** AP242 Domain Model XML (`.stpx`/`.xml`, ISO 10303-4442) | XML 경로는 동일 PartNode IR 로 합류 |

---

## 0.1 이 변환기가 만드는 것 — 그리고 만들지 않는 것

(논문 Introduction / 기여 범위에 해당.)

step2aas 가 산출하는 것은 **설계 시점 Type AAS** 다. STEP 은 제조 일련번호·센서
스트림·작업 지시를 담지 않으므로, 출력은 공장에서 바로 돌아가는 “살아있는 트윈”이
아니라 **그 트윈이 참조할 마스터 타입**이다 (갭 G6, `AssetKind.TYPE`).

이 구분은 단점이 아니라 연결 지점이다. AAS 메타모델은 한 자산에 서브모델을
더하는 것을 허용하고, 02011 BOM 의 `globalAssetId` 는 타입 AAS 를
다른 구조 Entity와 연결하는 훅이다. 타입과 인스턴스는 서로 다른 자산이며,
타입 관계에는 `derivedFrom`(AAS)과 `assetType`(자산)을 사용한다. 변환기는 CAD 쪽에서 그
훅을 채우고, 런타임 데이터는 다른 도구가 같은 셸에 붙인다.

---

## 1. XDE 추출 인벤토리 (변환기 입력 측)

| # | 항목 | STEP 엔티티 (AIM) | 추출 경로 | 비고 |
|---|---|---|---|---|
| S1 | 제품 계층 트리 | `next_assembly_usage_occurrence` (NAUO) | `XCAFDoc_ShapeTool` label 트리 재귀 | 어셈블리/서브어셈블리/부품 |
| S2 | 인스턴스 변환행렬 | `item_defined_transformation` | component label 의 `XCAFDoc_ShapeTool.GetLocation` | 4×4 배치 행렬 |
| S3 | 부품명 | `product.name` | `TDataStd_Name` | idShort 생성 원천 |
| S4 | 부품 식별자 | `product.id` | 〃 | AAS id 시드 |
| S5 | 반복 부품 수량 | NAUO 카운트 | 동일 referred label 참조 수 | BulkCount 원천 |
| S6 | 형상 (B-rep) | `advanced_brep_shape_representation` 등 | label → `TopoDS_Shape` | 미리보기 렌더(S15) 및 GProp 계산 입력 |
| S7 | Validation properties (GVP) | `measure_representation_item` (volume, surface area, centroid) | XDE 접근 가능 여부 M0 스파이크에서 확인 ⚠️ 부재/미지원 시 S6 에서 `BRepGProp` 계산으로 대체 | AP242 권장실무 기준 |
| S8 | 재질 | `material_designation` | `XCAFDoc_MaterialTool` | 밀도 동반 시 질량 계산 가능 |
| S9 | 색상/레이어 | `styled_item` 등 | `XCAFDoc_ColorTool` | 미리보기 및 부품 단위 STEP 재수출 시 면 색 유지. AAS 요소로는 매핑하지 않음 |
| S10 | 단위계 | `named_unit` 컨텍스트 | STEPControl 단위 설정 | LengthUnit 원천 |
| S11 | 헤더 메타데이터 | P21 HEADER (`FILE_NAME`: author, organization, originating_system, timestamp; `FILE_SCHEMA`) | **텍스트 직접 파싱** (extract/p21_header.py) | Nameplate 부분 매핑 + AP 판별 원천 |
| S12 | 바운딩 박스 | (파생 계산) | `Bnd_Box` on TopoDS_Shape | STEP에 없음 — 변환기가 계산 |
| S13 | 승인/조직 정보 | `applied_organization_assignment` 등 | XDE 미지원, 직접 파싱 필요 | **1차 스코프 제외** |
| S14 | Semantic PMI (GD&T) | `geometric_tolerance` 계열 | `XCAFDoc_DimTolTool` | XDE가 해석한 치수·기하공차의 존재 탐지. C21은 원본 파일 provision에만 조건부 사용하며, 완전한 PMI 목록/적합성 검사는 아님 (G7) |
| S15 | 미리보기 이미지 | (파생 생성) | pythonocc 오프스크린 렌더러 → PNG 512² | PreviewFile(C11) 원천. 렌더 실패 시 placeholder (D6) |

---

## 2. 매핑 테이블 A — IDTA 02011 Hierarchical Structures (BOM)

**대상 서브모델:** `HierarchicalStructures`
**Submodel semanticId:** `https://admin-shell.io/idta/HierarchicalStructures/1/1/Submodel` ✅ 02011-1-1
**소유 AAS:** 각 어셈블리 AAS (hierarchical: 서브어셈블리마다 1개)

| # | STEP 소스 | → IDTA 요소 [타입] | semanticId (경로) | 변환 규칙 | 카디널리티 |
|---|---|---|---|---|---|
| A1 | 루트 product (S1) | `EntryNode` [Entity] | `.../HierarchicalStructures/EntryNode/1/0` | 어셈블리 최상위 product → EntryNode. globalAssetId = 어셈블리 AAS의 assetId | 1 |
| A2 | 하위 NAUO 각각 (S1) | `Node` [Entity] | `.../HierarchicalStructures/Node/1/0` | 발생(occurrence)마다 Node 1개. hierarchical는 직계만, flat/single은 재귀 중첩. 대상 AAS가 있으면 Self-managed, 없으면 Co-managed | 0..* |
| A3 | 부모→자식 관계 (S1) | `HasPart` [Rel] | `.../HierarchicalStructures/HasPart/1/0` | 상위 Node → 하위 Node RelationshipElement | Node당 0..* |
| A4 | 부품/서브어셈블리 자산 연결 (S4) | Entity.globalAssetId | AAS metamodel 속성 | 자체 AAS가 있는 Node만: hierarchical 직계 자식, flat 리프. single 자식은 co-managed. optional SameAs 생략 | self-managed Node당 1 |
| A5 | 동일 부품 수량 (S5) | `BulkCount` [Prop, ULong] | `.../HierarchicalStructures/BulkCount/1/0` | 기본 출력에는 없음 (D2). `evaluate.py` 의 bulk 전략에서만 | 0..1 |
| A6 | (상수) | `ArcheType` [Prop, String] | `.../HierarchicalStructures/ArcheType/1/0` | hierarchical=`"OneDown"`, flat/single=`"Full"` (02011 ValueList) | 1 |
| A7 | 인스턴스 변환행렬 (S2) | — **매핑 없음** | — | 02011은 공간 배치를 다루지 않음 → 갭 G1 | — |

---

## 3. 매핑 테이블 B — IDTA 02003 Generic Frame for Technical Data (v1.2)

**대상 서브모델:** `TechnicalData`
**Submodel semanticId:** `https://admin-shell.io/ZVEI/TechnicalData/Submodel/1/2` ✅ 02003-1-2 (v2.0 ECLASS IRDI 로 이전하지 않음)
**소유 AAS:** 각 부품 AAS (어셈블리 AAS에도 합산치 버전 — D3)

| # | STEP 소스 | → IDTA 요소 [타입] | 섹션 | 변환 규칙 | 비고 |
|---|---|---|---|---|---|
| B1 | 부품명 (S3) | `ManufacturerProductDesignation` [MLP] | GeneralInformation | product.name → en 로케일 | 필수 필드 |
| B2 | 조직명 (S11) | `ManufacturerName` [Prop] | GeneralInformation | P21 헤더 organization. 없으면 `"unknown"` + 갭 G2 | ECLASS IRDI `0173-1#02-AAO677#002` |
| B3 | GVP 부피 (S7) | `Volume` [Prop, Double] | TechnicalProperties | mm³ 정규화. GVP 부재 시 `BRepGProp` 계산, 출처를 provenance Qualifier(`gvp`/`computed`)로 구분 | 자체 semanticId |
| B4 | GVP 표면적 (S7) | `SurfaceArea` [Prop, Double] | TechnicalProperties | 〃 mm² | 〃 |
| B5 | GVP 중심점 (S7) | `Centroid` [SMC{X,Y,Z}] | TechnicalProperties | 〃 mm, 부품 로컬 좌표계 | 〃 |
| B6 | 재질 (S8) | `Material` [Prop, String] | TechnicalProperties | material_designation.name 그대로 | ECLASS 매핑은 갭 G3 |
| B7 | 밀도 × 부피 | `Mass` [Prop, Double] | TechnicalProperties | 밀도 존재 시에만 (kg) | 파생값 Qualifier 명시 |
| B8 | 바운딩 박스 (S12) | — **02003에는 기입 안 함** | — | D4: 02026 Geometry 쪽에만 | |
| B9 | 단위계 (S10) | (개별 Prop unit 속성) | — | 모든 수치 SI(mm 계열) 정규화 | |

> TechnicalProperties 하위 자체 semanticId 는 `https://step2aas.org/props/...` 네임스페이스로
> 정의하고 논문에서 "표준 어휘 부재" 갭으로 논의. ECLASS 대응 확인 항목은 IRDI 교체.

---

## 4. 매핑 테이블 C — IDTA 02026 Provision of 3D Models (v1.0) ✅

**대상 서브모델:** `Models3D`
**Submodel semanticId:** `https://admin-shell.io/idta/Models3D/1/0` ✅
**소유 AAS:** 각 부품 AAS + 어셈블리 AAS (어셈블리 전체 STEP)

구조: `[SM] Models3D → [SML] Model3D → [SMC] {File, Capability, Geometry}`
부품당 **Model3D 항목 1개** (원본 STEP). *v0.1의 파생 X3D 항목(구 C13~C17)은 §6a future work 로 이동.*

### 4.1 [SMC] File — 원본 STEP

| # | STEP 소스 | → IDTA 요소 | semanticId 말단 | 값/규칙 |
|---|---|---|---|---|
| C1 | 파일 자체 (S6) | `FileVersion/DigitalFile` [File] | `.../FileVersion/DigitalFile/1/0` | 부품 단위 분리 저장 .stp. MIME: 공식 `model/step` 미등록 → `application/step` 관행 vs `application/octet-stream`(스펙 fallback 지시) 중 결정 ⚠️ |
| C2 | product.id (S4) | `FileId/{FileDomainId, ValueId}` | `.../FileId/.../1/0` | DomainId=`"STEP"`, ValueId=product.id, IsPrimary=true |
| C3 | 부품명 (S3) | `FileVersion/Title` [MLP] | `.../FileVersion/Title/1/0` | product.name@en |
| C4 | 파일명 | `FileVersion/FileName` [Prop] | `.../FileVersion/FileName/1/0` | `{safe_name}.stp` |
| C5 | (상수) | `FileVersion/FileVersionId` [Prop] | `.../FileVersionId/1/0` | 정책 D5 |
| C6 | (상수) | `FileVersion/StatusValue` [Prop] | `.../StatusValue/1/0` | ValueList: `"Released"` |
| C7 | 헤더 timestamp (S11) | `FileVersion/SetDate` [Prop, Date] | `.../SetDate/1/0` | YYYY-MM-dd |
| C8 | FILE_SCHEMA (S11) | `FileVersion/FileFormat/{FormatName, FormatVersion, FormatQualifier}` | `.../FileFormat/.../1/0` | `"STEP"` / `"AP242"` 등 / 스펙 예시 형식 `"STEP-2.03"` |
| C9 | originating_system (S11) | `FileVersion/SourceApplication/{ApplicationName, ApplicationVersion, VendorOrganization}` | `.../SourceApplication/.../1/0` | VendorOrganization[1] 필수 → originating_system 재사용 또는 `"unknown"` (갭 G2) |
| C10 | 조직 (S11) | `FileVersion/ProvidingOrganization` [SMC] | `.../ProvidingOrganization/1/0` | 헤더 organization |
| C11 | 미리보기 (S15) | `FileVersion/PreviewFile` [File, **필수[1]**] | `.../PreviewFile/1/0` | pythonocc 오프스크린 렌더 PNG(<512²). 실패 시 placeholder (D6) |
| C12 | (분류) | `FileClassification/{ClassId, ClassName, ClassificationSystem}` [필수[1]] | `.../FileClassification/.../1/0` | 1차: 자체 분류 `"CAD-3D"` — 갭 G4 |

### 4.2 [SMC] Capability

| # | STEP 소스 | → IDTA 요소 | 값/규칙 |
|---|---|---|---|
| C18 | 어셈블리 여부 (S1) | `ObjectType` [Prop] | ValueList: `"Assembly"` / `"Component"` |
| C19 | (상수) | `Origin` [Prop, 필수] | ValueList: `"DesignEngineering"` |
| C20 | (상수) | `PosModelPurpose` [SML, 필수] | ValueList 에 "정밀 마스터 형상" 개념 없음 → 갭 G5. 잠정: `"DesignEngineering"` 계열 값 재검토 ⚠️ |
| C21 | PMI 존재 (S14) | `EmbeddedInfo` [SML] | 탐지 근거가 있고 DigitalFile이 원본 바이트/원본 링크일 때만 `"PMI"`. 재생성 부품·서브어셈블리·형상 누락은 미방출; PMI 보존 적합성을 뜻하지 않음 |

### 4.3 [SMC] Geometry

| # | STEP 소스 | → IDTA 요소 | 값/규칙 |
|---|---|---|---|
| C22 | B-rep | `Representation` [Prop, 필수] | ValueList: `"SolidBody"` |
| C23 | 단위 (S10) | `LengthUnit` [Prop, 필수] | `"mm"` |
| C24 | 바운딩 박스 (S12) | `CartBoundingBox/{BoundingBoxKind, CartBoundingVector}` | Kind=`"MinEnvelope"`, AABB |
| C25 | 인스턴스 배치 (S2) | — **매핑 없음** (D8) | — | 02026 CartRefSystem 은 offset+법선만 받아 4×4 를 담지 못함 (G1). 어셈블리 Models3D 에 비표준 `PlacementScene` JSON 을 supplementary file 로 제공 | — |

---

## 5. 매핑 테이블 D — IDTA 02006 Digital Nameplate (부분 매핑)

**의도적 partial**: STEP이 채울 수 없는 필수 필드의 존재 자체가 논문 기여(라이프사이클 불일치 논증).

| # | STEP 소스 | → Nameplate 요소 | 상태 |
|---|---|---|---|
| D-1 | 헤더 organization | `ManufacturerName` | ✅ (설계 조직 ≠ 제조사 한계 명시) |
| D-2 | product.name | `ManufacturerProductDesignation` | ✅ |
| D-3 | 헤더 timestamp | `YearOfConstruction` (필수) | ⚠️ 의미론 불일치 — 갭 G6 |
| D-4 | — | `URIOfTheProduct` (필수) | ❌ → 정책 D7: Nameplate 서브모델 생략 |
| D-5 | — | `ContactInformation`, `SerialNumber` 등 | ❌ 갭 G6 |

---

## 6. 갭 분석 (논문 Discussion 재료)

| ID | 갭 | 성격 | 취급 |
|---|---|---|---|
| G1 | **공간 배치(변환행렬)의 표준 표현 부재** — 02011은 위상만, 02026 Geometry는 단일 참조계만 | 표준 공백 | 핵심 발견. 02026의 "형상은 파일에" 철학과 정합함을 논의, IDTA 제안 가능성. **조사 결과 아래 주석 참조** |

> **G1 신규성 조사 (2026-08-19).** 공개된 IDTA 템플릿 중 어셈블리 내부의 부품 배치를
> 담을 슬롯은 없음을 확인했다.
> - **IDTA 02045-1-0 Data Model for Asset Location** (2024-07): 위치 좌표와 좌표계를
>   갖지만 스코프가 **물류·구내 위치**(우편주소, 홀/통로, GNSS)이고, 스펙 본문에
>   *"This Submodel is not supporting 6DoF orientation information for now"* 라고
>   **명시**한다. CAD·assembly·행렬 언급 0건 (31쪽 전문 검색).
>   → 갭이 IDTA 스스로 인정한 것이라는 더 강한 근거. 논문 §3.4 + 참고문헌 [34] 로 반영.
> - **IDTA 02026-1-0** 자체의 `CartRefSystem` 은 origin + 법선만 받아 pose 를 담지 못함 (D8, 기존 확인).
> - 02026 은 여전히 1-0 이 최신 (v1.1 없음) — 논문 Table 1 의 버전 핀 유효.
> - `admin-shell-io/submodel-templates` 공개 목록에 geometry/placement/pose/kinematics
>   전용 템플릿 없음. IDTA 02062 *Interface Connectors* 는 "In Review" 로 내용 비공개 —
>   **검증 불가하므로 논문에 인용하지 않음**. 향후 이 템플릿이 공개되면 G1 과의 관계를 재확인할 것.
> - 학술 검색에서도 AAS ↔ 어셈블리 배치 변환행렬 갭을 다룬 선행 연구를 찾지 못함
>   (부재 증명은 아님).

| G2 | P21 헤더 메타데이터의 관행적 빈약함 | 데이터 품질 | fallback 정책 기술 |
| G3 | STEP 재질 명칭 ↔ ECLASS 어휘 불일치 | 의미론 정렬 | 문자열 유지 + 확장 여지 |
| G4 | 3D CAD 파일 분류 체계 표준 부재 | 표준 공백 | 자체 분류 명시 |
| G5 | 02026 ModelPurpose ValueList 에 authoritative geometry 개념 부재 | 표준 공백 | ValueList 확장 제안 |
| G6 | 설계 시점(STEP) vs 제조 시점(Nameplate) 라이프사이클 불일치 | 개념적 | **Type AAS** 산출이 적절하다는 논증으로 연결 |
| G7 | Semantic PMI → 02049 품질 매핑 | 스코프 외 | Future work |

### 6a. Future work (v0.1 에서 이동)

- **경량 형상 파생**: 부품별 X3D/glTF 를 두 번째 Model3D 항목으로 추가하고
  `FileVersion/BasedOn` + `Capability/Simplification/DerivedFrom` 으로 원본 STEP 과의
  파생 관계를 표현 (구 C13~C17). STP2X3D 연동 또는 OCCT 테셀레이션 직접 구현.
  → 후속 논문 후보: 정밀↔경량 이중 표현의 traceability.
- ~~AP242 Domain Model XML 입력~~ → **구현 완료** (`extract/ap242xml.py`, ISO 10303-4442 ed-3
  CAx-IF/MBx-IF Assembly Structure; all-in-one 및 nested 다중파일 지원). 동일 PartNode IR 로
  파싱하여 §2/§3/§4 매핑 전부 재사용. semantic PMI → IDTA 02049 매핑은 여전히 future work.

---

## 7. 설계 결정 (D1~D8 — 확정, §8 체크리스트 참조)

| ID | 질문 | 채택안 |
|---|---|---|
| D1 | 부품 AAS idShort 명명 | product.name 정규화 + 충돌 시 접미사 |
| D2 | 동일 부품 n회: Node n개 vs 1개+BulkCount | **Node n개** (occurrence 별 globalAssetId로 동일 prototype 자산 식별). BulkCount 인코딩은 `scripts/evaluate.py` 가 크기 비교용으로만 생성 |
| D3 | 어셈블리 AAS 의 TechnicalData | 생성 (속성별 전체 하위 값이 있을 때만 부피/면적 합계, computed Qualifier; 일부 누락이면 해당 합계 생략) |
| D4 | 바운딩 박스 위치 | **02026 Geometry 에만** |
| D5 | 재변환 시 FileVersionId | 입력 fingerprint 앞 12자(P21 원본 SHA-256, XML v2 dependency manifest hash). 전체해시는 provenance에 기록 |
| D6 | PreviewFile 생성 | **pythonocc 오프스크린 렌더러 확정.** 헤드리스 OpenGL 실패 시 단색 placeholder PNG fallback 필수 |
| D7 | 필수 필드 미충족 처리 | Nameplate 생략 / 02026 내 필수: fallback 상수 + provenance Qualifier |
| D8 | 인스턴스 4×4 의 표준 슬롯 | **C25 CartRefSystem 을 내보내지 않음.** 어셈블리 `PlacementScene` JSON 이 G1 의 provision artifact |

---

## 8. 다음 단계 체크리스트

- [x] semanticId 대조: 02011-1-1, 02003-1-2, 02006-2-0, 02026-1-0 (최신 메이저 버전으로의 이전은 보류)
- [ ] admin-shell-io/submodel-templates 샘플 AASX 와 Package Explorer 구조 비교 (사용자 GUI)
- [x] M0 스파이크: XDE 추출 S1~S12 / S14 (GVP 는 파일에서 못 읽고 BRepGProp)
- [x] D1~D8 확정 (본 절 §7)
- [ ] 갭 G1 신규성의 체계적 문헌 검토 — 검토한 템플릿 범위의 공백과 전체 신규성은 구분
- [x] AP242 Domain Model XML 입력
- [ ] M4: fixtures 전체 평가 CSV + (선택) BaSyx 업로드 데모 — `scripts/evaluate.py`, `scripts/demo_basyx.py`

---

## 9. Downstream 활용 시나리오 (논문 Discussion / Outlook)

변환기의 가치는 `.aasx` 파일을 만드는 데 그치지 않고, **그 패키지가 AAS 생태계의
어디에 꽂히는가**에 있다. 아래는 구현 범위 밖의 사용이며, 논문에서 도구의
포텐셜을 말할 때 Introduction 말미와 Discussion 에 둘 수 있는 시나리오다.

### 9.1 공장·라인 디지털 트윈의 뼈대

생산 라인 AAS 는 보통 설비 인스턴스(컨베이어, 로봇 셀, AGV)의 집합이다. 그
설비가 조립하는 **제품 타입** 의 형상·BOM·기술 데이터는 CAD 에 있고, 지금까지는
트윈과 별도 PLM 에 갇혀 있었다. step2aas 출력의 어셈블리 AAS(02011) 를 라인
AAS 의 제품 슬롯에 별도 생산 할당 관계로 연결하면, 라인 트윈이 “지금 이 스테이션이
어떤 타입을 조립 중인가”를 표준 식별자로 가리킬 수 있다. 부품 AAS 는 공급사
카탈로그 트윈과 연결하려면 별도 공통 식별자 매핑이 필요하다. 제품 생산 할당 관계는
물리적 부품 관계인 HasPart와 구분하여 정의해야 한다.

### 9.2 설계 타입 AAS ⊕ 운영 인스턴스 AAS

운영 측은 이미 (또는 앞으로) 다음을 담는 **Instance AAS** 를 만든다.

- 시리얼 번호·명판 — 02006 Digital Nameplate (본 변환기는 설계 데이터만으로는
  필수 필드를 못 채워 생략한다. G6. 인스턴스 쪽에서 채우는 것이 맞다.)
- OPC UA / MQTT 시계열 — IDTA 02007 Time Series Data, 또는 벤더 서브모델
- 유지보수·고장 — 02012, 02013 계열
- 탄소·에너지 — 관련 IDTA 템플릿

두 AAS를 연결할 때 인스턴스 AAS의 `derivedFrom`은 타입 AAS ID를 참조하고,
인스턴스 자산의 `assetType`은 타입 `globalAssetId`를 가리킬 수 있다.
타입과 인스턴스 자산의 동일성을 SameAs로 선언하지 않는다. 관제 UI와 discovery가
이를 해석하는 통합은 별도로 구현해야 한다. 근거:
[IDTA 공식 FAQ](https://industrialdigitaltwin.io/questions-and-answers/).

### 9.3 공급망: 부품 AAS 를 카탈로그로

§0 의 “부품별 AAS 1개”는 공급망 시나리오를 염두에 둔 것이다. 동일 브래킷이
한 입력 안에 반복되면 D2로 AAS는 하나다. 서로 다른 세 파일·제품 사이의 동일성은
현재 파일 해시 기반 ID가 보장하지 않는다. 공통 식별자 서비스를 추가하면 OEM은 BOM을 배포하고, 브래킷
AAS 는 공급사가 갱신한다(재질 리비전, 경량 glTF 추가 — §6a). 수신 측은 assetId
로 최신 타입을 해석한다.

### 9.4 커미셔닝·시뮬레이션

02026 에 임베드된 STEP 과 PlacementScene(D8) 은 오프라인 3D 뷰어
(`python -m step2aas.viewer`)뿐 아니라 로봇 오프라인 프로그래밍·디지털
커미셔닝 도구의 입력이다. 경량 파생(§6a)이 붙으면 같은 BasedOn 링크로 브라우저
WebGL 과 정밀 B-rep 을 오갈 수 있다. 이 경로의 실시간 제어 루프는 본 도구
밖이다.

### 9.5 이 도구가 하지 않는 것

- 공장 현장 데이터 수집, OPC UA 구독, 대시보드 서버
- 인스턴스 일련번호·URIOfTheProduct 발급 (D7)
- PMI 공차 → 품질 서브모델 02049 (G7, future work)

논문의 주장은 “CAD 를 트윈으로 통째 대체한다”가 아니라, **설계 산출물을 AAS
타입 계층에 올려 운영 트윈이 붙을 자리를 만든다**는 것이다.

## 2026-09-07 C22 형상 표현 정책

C22는 상수 SolidBody를 일괄 적용하지 않는다. 실제 OCCT topology로
SolidBody / WireFrame / Surface / Mesh / PointCloud를 분류한다.
분류 불가 또는 서로 다른 종류가 섞인 형상은 optional Geometry 전체를 생략하고 경고한다.
이는 IDTA 02026-1-0 Table 3(p19)의 Geometry 0..1, Table 73(p77)의
Geometry 내 Representation 1, Table 107(p109)의 value list를 따른다.

canonical YAML의 새 `constants.RepresentationKinds`를 사용한다.
이전 `Representation` 문자열은 deprecated이며 fallback으로 사용하지 않는다.
`STEP2AAS_MAPPING_DIR`에서 전체 규칙을 제공하는 사용자는 새 분류표를 포함해야 한다.
원본 STEP과 preview 제공, 별도 TechnicalData 값의 추출은 이 optional 생략과 구별한다.
