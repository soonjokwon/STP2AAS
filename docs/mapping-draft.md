# STEP → AAS 매핑 초안 (v0.2)

**프로젝트:** STEP to Twin — STEP 모델로부터 AAS 디지털 트윈 생성
**개정일:** 2026-07-23 (v0.1 → v0.2: pythonocc 단독 아키텍처 확정, 경량 형상 파생을 future work 로 이동)
**상태:** 초안 — 스펙 원문 대조 필요 항목은 ⚠️ 표기 (02026 은 원문 확인 완료 ✅)

이 문서는 세 가지 용도를 겸한다: (1) 변환기 구현 명세, (2) 논문 Table 1(매핑 정의)의
기초 자료, (3) 매핑 불가 항목의 갭 분석(논문 Discussion 재료).

---

## 0. 전제: AAS 구성 전략

| 결정 사항 | 채택안 | 근거 |
|---|---|---|
| AAS 단위 | **부품(part)별 AAS 1개 + 어셈블리 AAS 1개** (기본 hierarchical; 아래 구성 모드 참조) | AAS 철학상 자산 단위가 부품. 부품 재사용/공급망 시나리오에 자연스러움 |
| 어셈블리 구조 표현 | 어셈블리 AAS의 02011 서브모델이 EntryNode가 되고, 하위 Node가 SameAs로 부품 AAS를 참조 | 02011 권장 패턴 |
| AAS id 생성 | `urn:stp2aas:{uuid5}` — STEP `product.id` + 파일 해시 기반 결정론적 UUID | 재변환 시 동일 id 보장 (idempotency) |
| 구성 모드 (`--assembly-structure`) | **hierarchical**(기본): 고유 부품·고유 서브어셈블리마다 AAS, 각 어셈블리 BOM은 직계 자식만(A6 ArcheType=`OneDown`), Node는 자식 AAS 자산으로 SameAs. **flat**: 루트 AAS 1개 + 고유 부품 AAS, BOM은 전체 트리 중첩(ArcheType=`Full`), 자체 AAS가 없는 중간 서브어셈블리 Node는 co-managed(globalAssetId·SameAs 없음). **single**: 전체 모델이 AAS 1개, 모든 부품은 Model3D 엔트리, BOM 전체 트리는 co-managed(ArcheType=`Full`) | 사용처별 트레이드오프(표준 충실 vs 패키지 단순성) 비교 실험 지원 |
| 모드 간 id 규칙 | 루트 **globalAssetId는 세 모드 공통**(동일 물리 자산), 루트 **AAS/서브모델 id는 모드별 상이**(내용이 다른 셸의 공존 허용). 부품 AAS id는 hierarchical/flat 간 공유(내용 동일, D2) | 같은 파일의 여러 모드 산출물을 한 저장소에 동시 적재 가능 |
| 형상 파일 배치 | **원본 STEP만** 부품 단위로 분리하여 AASX supplementary file 로 임베드. 경량 포맷(X3D/glTF) 파생은 future work — CLI 에 `--derive-lightweight` 슬롯만 예약 | 02026 취지가 "형상 재정의가 아닌 제공(provision)". 원본만으로 템플릿 요구 충족 |
| 추출 엔진 | **pythonocc-core (XDE/XCAF) 단독** + P21 헤더 텍스트 파싱. XDE 순회 로직은 STP2X3D(C++)의 구조를 이식 | 단일 언어 저장소, 미리보기 렌더까지 동일 스택에서 해결 |
| 대상 스키마 | AP203/AP214/AP242 (P21) | XDE가 세 AP 모두 흡수. AP242 XML은 후속 연구 |

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
| S9 | 색상/레이어 | `styled_item` 등 | `XCAFDoc_ColorTool` | 미리보기 렌더 품질용. AAS 매핑은 안 함 |
| S10 | 단위계 | `named_unit` 컨텍스트 | STEPControl 단위 설정 | LengthUnit 원천 |
| S11 | 헤더 메타데이터 | P21 HEADER (`FILE_NAME`: author, organization, originating_system, timestamp; `FILE_SCHEMA`) | **텍스트 직접 파싱** (extract/p21_header.py) | Nameplate 부분 매핑 + AP 판별 원천 |
| S12 | 바운딩 박스 | (파생 계산) | `Bnd_Box` on TopoDS_Shape | STEP에 없음 — 변환기가 계산 |
| S13 | 승인/조직 정보 | `applied_organization_assignment` 등 | XDE 미지원, 직접 파싱 필요 | **1차 스코프 제외** |
| S14 | Semantic PMI (GD&T) | `geometric_tolerance` 계열 | `XCAFDoc_DimTolTool` | 존재 여부 플래그만 1차 사용(C21). 내용 매핑은 future work (G7) |
| S15 | 미리보기 이미지 | (파생 생성) | pythonocc 오프스크린 렌더러 → PNG 512² | PreviewFile(C11) 원천. 렌더 실패 시 placeholder (D6) |

---

## 2. 매핑 테이블 A — IDTA 02011 Hierarchical Structures (BOM)

**대상 서브모델:** `HierarchicalStructures`
**Submodel semanticId:** `https://admin-shell.io/idta/HierarchicalStructures/1/1/Submodel` ⚠️ v1.1 스펙 재확인
**소유 AAS:** 어셈블리 AAS

| # | STEP 소스 | → IDTA 요소 [타입] | semanticId (경로) | 변환 규칙 | 카디널리티 |
|---|---|---|---|---|---|
| A1 | 루트 product (S1) | `EntryNode` [Entity] | `.../HierarchicalStructures/EntryNode/1/0` ⚠️ | 어셈블리 최상위 product → EntryNode. globalAssetId = 어셈블리 AAS의 assetId | 1 |
| A2 | 하위 NAUO 각각 (S1) | `Node` [Entity] | `.../HierarchicalStructures/Node/1/0` ⚠️ | 서브어셈블리/부품 발생(occurrence)마다 Node 1개. 재귀 중첩 | 0..* |
| A3 | 부모→자식 관계 (S1) | `HasPart` [Rel] | `.../HierarchicalStructures/HasPart/1/0` ⚠️ | 상위 Node → 하위 Node RelationshipElement | Node당 0..* |
| A4 | 부품 AAS 연결 (S4) | `SameAs` [Rel] | `.../HierarchicalStructures/SameAs/1/0` ⚠️ | Node → 해당 부품 AAS의 globalAssetId 참조 | Node당 0..1 |
| A5 | 동일 부품 수량 (S5) | `BulkCount` [Prop, ULong] | `.../HierarchicalStructures/BulkCount/1/0` ⚠️ | 설계 결정 D2 참조 | 0..1 |
| A6 | (상수) | `ArcheType` [Prop, String] | `.../HierarchicalStructures/ArcheType/1/0` ⚠️ | 구성 모드별: 전체 트리 중첩(flat/single)=`"Full"`, 직계 자식만(hierarchical)=`"OneDown"` ⚠️ ValueList 리터럴 스펙 대조 필요 | 1 |
| A7 | 인스턴스 변환행렬 (S2) | — **매핑 없음** | — | 02011은 공간 배치를 다루지 않음 → 갭 G1 | — |

---

## 3. 매핑 테이블 B — IDTA 02003 Generic Frame for Technical Data (v1.2)

**대상 서브모델:** `TechnicalData`
**Submodel semanticId:** `https://admin-shell.io/ZVEI/TechnicalData/Submodel/1/2` ⚠️ 재확인
**소유 AAS:** 각 부품 AAS (어셈블리 AAS에도 합산치 버전 — D3)

| # | STEP 소스 | → IDTA 요소 [타입] | 섹션 | 변환 규칙 | 비고 |
|---|---|---|---|---|---|
| B1 | 부품명 (S3) | `ManufacturerProductDesignation` [MLP] | GeneralInformation | product.name → en 로케일 | 필수 필드 |
| B2 | 조직명 (S11) | `ManufacturerName` [Prop] | GeneralInformation | P21 헤더 organization. 없으면 `"unknown"` + 갭 G2 | ECLASS IRDI ⚠️ |
| B3 | GVP 부피 (S7) | `Volume` [Prop, Double] | TechnicalProperties | mm³ 정규화. GVP 부재 시 `BRepGProp` 계산, 출처를 provenance Qualifier(`gvp`/`computed`)로 구분 | 자체 semanticId |
| B4 | GVP 표면적 (S7) | `SurfaceArea` [Prop, Double] | TechnicalProperties | 〃 mm² | 〃 |
| B5 | GVP 중심점 (S7) | `Centroid` [SMC{X,Y,Z}] | TechnicalProperties | 〃 mm, 부품 로컬 좌표계 | 〃 |
| B6 | 재질 (S8) | `Material` [Prop, String] | TechnicalProperties | material_designation.name 그대로 | ECLASS 매핑은 갭 G3 |
| B7 | 밀도 × 부피 | `Mass` [Prop, Double] | TechnicalProperties | 밀도 존재 시에만 (kg) | 파생값 Qualifier 명시 |
| B8 | 바운딩 박스 (S12) | — **02003에는 기입 안 함** | — | D4: 02026 Geometry 쪽에만 | |
| B9 | 단위계 (S10) | (개별 Prop unit 속성) | — | 모든 수치 SI(mm 계열) 정규화 | |

> TechnicalProperties 하위 자체 semanticId 는 `https://stp2aas.org/props/...` 네임스페이스로
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
| C21 | PMI 존재 (S14) | `EmbeddedInfo` [SML] | DimTol 존재 시 `"PMI"` — 저비용·고가치 |

### 4.3 [SMC] Geometry

| # | STEP 소스 | → IDTA 요소 | 값/규칙 |
|---|---|---|---|
| C22 | B-rep | `Representation` [Prop, 필수] | ValueList: `"SolidBody"` |
| C23 | 단위 (S10) | `LengthUnit` [Prop, 필수] | `"mm"` |
| C24 | 바운딩 박스 (S12) | `CartBoundingBox/{BoundingBoxKind, CartBoundingVector}` | Kind=`"MinEnvelope"`, AABB |
| C25 | 인스턴스 배치 (S2) | `CartRefSystem/{CartOffsetVector, NormOrientationVector}` | **부분 매핑** — 완전한 4×4 불가 → 갭 G1 |

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
| G1 | **공간 배치(변환행렬)의 표준 표현 부재** — 02011은 위상만, 02026 Geometry는 단일 참조계만 | 표준 공백 | 핵심 발견. 02026의 "형상은 파일에" 철학과 정합함을 논의, IDTA 제안 가능성 |
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

## 7. 미결 설계 결정

| ID | 질문 | 잠정안 |
|---|---|---|
| D1 | 부품 AAS idShort 명명 | product.name 정규화 + 충돌 시 접미사 |
| D2 | 동일 부품 n회: Node n개 vs 1개+BulkCount | **Node n개** (occurrence 별 SameAs 동일 AAS). BulkCount 방식과의 크기 비교는 평가 실험거리 |
| D3 | 어셈블리 AAS 의 TechnicalData | 생성 (합산 질량/부피, 파생 Qualifier) |
| D4 | 바운딩 박스 위치 | **02026 Geometry 에만** |
| D5 | 재변환 시 FileVersionId | 입력 해시 불변 시 동일 버전 |
| D6 | PreviewFile 생성 | **pythonocc 오프스크린 렌더러 확정.** 헤드리스 OpenGL 실패 시 단색 placeholder PNG fallback 필수 |
| D7 | 필수 필드 미충족 처리 | Nameplate 생략 / 02026 내 필수: fallback 상수 + provenance Qualifier |

---

## 8. 다음 단계 체크리스트

- [ ] ⚠️ semanticId 전수 대조: 02011 v1.1, 02003 v1.2, 02006 스펙 PDF (02026 완료 ✅)
- [ ] admin-shell-io/submodel-templates 샘플 AASX 와 구조 비교
- [ ] M0 스파이크: NIST CTC 모델에서 S1~S12 추출 실증 (특히 S7 GVP 의 XDE 접근 가능 여부)
- [ ] D1~D7 확정 및 결정 로그 작성
- [ ] 갭 G1 의 기존 논의 여부 IDTA 백로그 조사 — 신규성 확인
