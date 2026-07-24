# semanticId 검증 로그

## semanticId 상태

| 항목 ID | semanticId | 상태 | 근거 (스펙 문서/페이지) | 일자 |
|---|---|---|---|---|
| C1~C25 (02026) | https://admin-shell.io/idta/Models3D/... | ✅ 검증 | IDTA 02026-1-0 스펙 원문 | 2026-07-23 |
| A1~A6 (02011) | https://admin-shell.io/idta/HierarchicalStructures/... | ⚠️ 미검증 | 코드에 `# VERIFY` 주석. 02011 v1.1 스펙 PDF 대조 필요 | 2026-07-23 |
| B0~B7 (02003) | https://admin-shell.io/ZVEI/TechnicalData/... + ECLASS IRDI | ⚠️ 미검증 | ManufacturerName/Designation IRDI, 섹션 semanticId 대조 필요 | 2026-07-23 |
| B3~B7 props | https://stp2aas.org/props/... | 자체 정의 | 표준 어휘 부재(갭 §3). 논문에서 논의 | 2026-07-23 |
| D (02006 Nameplate) | https://admin-shell.io/zvei/nameplate/2/0/... + IRDI | ⚠️ 미검증 | 기본 정책상 서브모델 생략(D7)이라 출력 경로에는 미포함 | 2026-07-23 |

⚠️ 항목은 `mapping/*.yaml` 및 매퍼 코드에 `# VERIFY(...)` 주석으로 표시돼 있음.
스펙 PDF 확보 후 IRDI/URL 교체하고 위 표를 갱신할 것.

## M0 스파이크 결과 (XDE 추출 인벤토리 S1~S14)

`extract/xde.py` 로 합성 fixture 및 실제 AP242 STEP(NX/Datakit) 부품에서 검증:

| 항목 | 상태 | 비고 |
|---|---|---|
| S1 제품 계층 (NAUO 트리) | ✅ | `GetFreeShapes` + `GetComponents` 재귀 (STP2X3D 구조 이식) |
| S2 인스턴스 변환행렬 | ✅ | `GetLocation().Transformation()` → 4×4 |
| S3 부품명 | ✅ | `TDF_Label.GetLabelName()` (pythonocc 편의 메서드) |
| S4 부품 식별자 | ⚠️ 부분 | STEP `product.id` 는 XDE 로 별도 노출 안 됨 → label entry 대체(갭 G2) |
| S5 반복 부품 수량 | ✅ | 동일 referred label(ref_key) 공유 카운트, D2 |
| S6 형상 (B-rep) | ✅ | `GetShape` |
| S7 GVP (부피/표면적/중심) | ⚠️→✅ | GVP 는 XDE 로 접근 불가 → `BRepGProp` 계산, provenance=`computed` (B3~B5) |
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

## 구성 모드 (hierarchical / flat / single) — 정합성 검토 (2026-07-24)

`--assembly-structure` 3종의 02011 정합성 검토·수정 결과.

| 항목 | 상태 | 내용 |
|---|---|---|
| A6 ArcheType | ⚠️ 수정 | hierarchical(직계 자식만)은 `"OneDown"`, flat/single(전체 트리)은 `"Full"` 로 구분 방출. **VERIFY(A6): 02011 v1.1 ValueList 리터럴("OneDown" 표기) 스펙 PDF 대조 필요** |
| flat SameAs dangling | ✅ 수정 | 자체 AAS 가 없는 중첩 서브어셈블리 Node 가 미방출 자산으로 SameAs 를 걸던 문제 → co-managed(globalAssetId/SameAs 없음) 로 변경. leaf Node 는 종전대로 부품 AAS 에 SameAs |
| 모드 간 id 충돌 | ✅ 수정 | 루트 AAS/서브모델 id 를 모드별로 분리(같은 파일의 여러 모드 산출물이 한 저장소에 공존 가능). 루트 globalAssetId 는 세 모드 공통(동일 자산). 부품 AAS 는 hierarchical/flat 간 공유 |
| 자동 검증 | ✅ | `tests/test_roundtrip.py::test_assembly_structure_modes` — 3모드 각각 SameAs 해석·ArcheType·엔티티 타입·모드 간 id 관계 assert. Vice(NX, 서브어셈블리 1) 실모델에서 dangling 0 확인 |

주의: 이 수정으로 flat/single 산출물의 루트 AAS id 와 single 의 asset id 가 이전 버전과 달라짐(부품 AAS id 는 불변).
