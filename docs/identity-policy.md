# 입력 식별자와 버전 정책

개정: 2026-09-07. 매핑 명세 D2(반복 prototype)·D5(재변환 식별자)의 구현 정책이다.

## 1. 식별자의 의미와 호환성

| 입력 | `identity_scheme` | `StepDocument.file_hash` |
|---|---|---|
| STEP Part 21 | `p21-sha256-v1` | 기존과 같은 원본 파일 바이트의 SHA-256 |
| AP242 XML | `ap242-dependency-v2` | 아래 의존 파일 manifest를 정규화한 JSON의 SHA-256 |

XML v2는 최상위 XML, 읽은 중첩 XML, 선택한 외부 STEP 형상 파일의 바이트와
해결한 상대 경로를 버전 근거로 삼는다. 원본 XML을 수정하지 않고 외부 STEP이나
중첩 XML만 바꿔도 `file_hash`가 달라진다. 참조한 파일이 없다가 생기는 경우도 구분한다.

이 값은 입력 묶음의 버전 fingerprint이며 지속적인 catalogue asset ID가 아니다.
현재 writer는 이 해시와 prototype `ref_key`로 AAS·asset·submodel ID를 파생한다.
따라서 의존 파일 하나가 바뀌면 같은 입력 묶음에서 파생한 다른 부품의 ID도 바뀐다.
`FileVersionId`는 이 해시의 앞 12자를 사용하는 짧은 표기다. 전체 해시와 동등한
충돌 저항성을 갖는 식별자나 제품 revision 번호로 취급하지 않는다.

P21 해시와 prototype 규칙은 변경하지 않는다. `StepDocument`에 추가한 선택 필드는
기존 필드 뒤에 배치했으며 기본값은 `source_manifest=[]`,
`identity_scheme="p21-sha256-v1"`, `legacy_file_hash=None`이다.

## 2. XML v2 manifest와 계산 규칙

`source_manifest`는 아래 문자열 필드를 가진 항목의 목록이다.

| 필드 | 의미 |
|---|---|
| `path` | 최상위 XML의 디렉터리를 기준으로 한 상대 경로. 구분자는 `/` |
| `role` | `xml` 또는 `geometry` |
| `status` | 파일이 있으면 `present`, 참조 대상 파일이 없으면 `missing` |
| `sha256` | 파일 원본 바이트의 소문자 SHA-256 64자리. `missing`이면 빈 문자열 |

정규화 절차는 다음과 같다.

1. 참조 경로의 `\`를 `/`로 정규화하고, **참조를 선언한 XML 파일의 디렉터리**에서
   실제 상대 경로를 해결한다. `../`를 보존해 해석하며 basename으로 축약하지 않는다.
2. 존재하는 파일은 실제 경로를 resolve한다. 정확한 경로가 없으면 각 디렉터리
   구성요소를 대소문자 구분 없이 비교하며, 후보가 둘 이상이면 명시적 오류를 낸다.
   이름이 같다는 이유로 최상위 XML 옆의 다른 파일을 대신 사용하지 않는다.
3. 해결한 경로를 최상위 XML 디렉터리에 상대화한다. 항목을 `(path, role)`로 중복
   제거하고 같은 쌍의 문자열 오름차순으로 정렬한다.
4. `{"identity_scheme":"ap242-dependency-v2","sources":manifest}`를
   Python `json.dumps(..., sort_keys=True, separators=(",", ":"),
   ensure_ascii=False)`로 직렬화한 UTF-8 바이트에 SHA-256을 적용한다.

`legacy_file_hash`에는 최상위 XML의 원본 SHA-256을 별도로 보존한다. 이는 예전
변환 결과를 대조하는 단서이며, 구식 해시가 외부 파일을 구분하지 못한 문제를
소급해서 해결하거나 이전/신규 객체 간 동치 관계를 증명하지 않는다.

## 3. 의존 파일 포함 범위

manifest는 디렉터리 전체 목록이나 모든 AP242 참조의 완전한 closure가 아니다.
현재 추출기가 선택한 root에서 도달한 구조와 파일 후보를 기록한다.

- 최상위 XML과 실제로 확장한 중첩 XML은 파일 전체 바이트를 포함한다.
- 도달한 leaf의 DocumentAssignment에서 인식한 `.stp`, `.step`, `.stpz`,
  `.stpx`, `.xml` 후보를 기존 후보 순서대로 조사한다. 처음 존재하는 지원 대상이
  선택되며, 그 전에 조사한 누락 후보도 `missing`으로 기록한다. 이후 후보는 조사하지 않는다.
- 재사용한 동일 파일은 manifest에 한 번만 포함한다. 같은 파일의 형상 로딩도 캐시한다.
- 외부 XML 순환 또는 기존 깊이 제한 때문에 확장을 멈춘 경계 XML도 해시한다.
  경계 파일 내부의 참조까지 추가로 탐색하지는 않는다.
- 형상 로딩이 실패해 fallback을 사용해도 선택한 형상 파일은 `present`와 바이트
  해시로 남는다. `present`는 CAD 파싱 성공이나 유효한 B-rep를 뜻하지 않는다.
- 선택하지 않은 root, 도달하지 않은 Part, 미지원 확장자, 사용하지 않은 추가
  geometry/document reference의 대상 파일은 별도로 탐색하지 않는다. 다만 이미 읽은
  XML의 바이트가 바뀌면 사용하지 않은 XML 요소를 바꾼 경우에도 해시는 달라진다.

기존 오류 처리는 유지한다. XML 내부 Part 순환·20개 노드 초과는 `ValueError`,
Part 없는 XML은 명시적 오류다. 외부 XML 순환·재귀 제한은 경고와 미확장 노드로
종료한다. 누락 형상과 CAD 로딩 실패는 기존 fallback 정책을 유지한다.

## 4. Prototype 경로 namespace

| 종류 | `ref_key` 예 |
|---|---|
| XML 내부 assembly 또는 geometry 없는 Part | `xml:sub/module.stpx#part_uid` |
| 외부 STEP prototype | `file:parts/bolt.stp` |
| 순환·깊이 제한으로 확장하지 않은 XML 경계 | `unexpanded:file:sub/module.stpx` |

경로와 UID는 URL percent encoding을 적용하고 경로의 `/`만 유지한다. 경로에 있는
`#`, `%`와 UID 구분자가 충돌하지 않는다. 같은 STEP 파일을 여러 위치에서 재사용하면
키가 같고, `left/bolt.stp`와 `right/bolt.stp`는 바이트가 같아도 서로 다른 prototype이다.
XML 파일명과 Part UID가 같아도 디렉터리가 다르면 다른 prototype이다.

동일한 디렉터리 구조·파일명·바이트·참조 가용성을 유지하여 입력 묶음 전체를 다른
위치로 이동하면 manifest, 해시, prototype 키가 같다. 파일명 변경, 디렉터리 내부
재배치, symlink 대상 변경은 같은 동작을 보장하는 이동이 아니다. 상위 폴더의 파일은
`../`로 표현할 수 있으며 그 의존 파일까지 같은 상대 구조로 옮겨야 한다.

절대 경로 참조를 이식 가능한 참조로 고쳐 쓰지는 않는다. Windows의 다른 드라이브나
다른 UNC share는 하나의 상대 기준으로 표현할 수 없으므로 지원 범위 밖이며 오류로
처리된다. 이동 가능한 입력 묶음에는 일반적인 상대 경로를 사용한다.

## 5. 이전 결과의 마이그레이션

기존 XML 출력의 해시는 최상위 XML만 반영했고, prototype 키는 XML stem 또는 STEP
basename만 사용했다. XML v2는 해시와 prototype 키를 함께 변경하는 의도적인
호환성 변경이다. **이전 XML 입력은 v2로 재변환해야 하며, 기존 AASX의 ID를
제자리에서 자동 재작성하지 않는다.** 원본과 예전 AASX는 유지한다.

1. 원본 XML과 참조 파일 묶음, 기존 AASX를 보관한다.
2. 현재 변환기로 재변환한 새 AASX를 별도 산출물로 만든다.
3. 기존 해시와 신규 `legacy_file_hash`, 원본 제품 정보, manifest를 함께 대조한다.
   예전 basename 충돌이 있었다면 old→new 매핑은 일대일이 아닐 수 있다.
4. 저장소에 새 객체로 적재하고, 소비자가 참조하던 ID 변경은 검토한 매핑으로 적용한다.
   검증하지 않은 `SameAs`나 catalogue identity를 자동 생성하지 않는다.

같은 AASX 파일 이름으로 덮어쓰는 것은 식별자 마이그레이션이 아니다. persistent
catalogue ID, 제품 revision 관계, 조직 간 identity reconciliation은 별도 정책이 필요하다.

## 6. 검증과 한계

`tests/test_xml_identity.py`는 외부 STEP 수정, 중첩 XML 수정, 디렉터리 이동,
동명 파일 분리, 반복 파일 재사용, missing→present, 상대 경로/Windows 구분자,
대소문자, 외부 순환·깊이, 형상 오류의 manifest 동작을 합성 입력으로 검증한다.
실제 CAD 형상 로딩은 이 테스트에서 분리하며 CAD 정밀도나 XML XSD 적합성을 주장하지 않는다.

변환 중 입력 파일이 다른 프로세스에 의해 바뀌지 않는 것을 전제로 한다. 이 기능은
원자적 snapshot을 만들지 않는다. 도구 버전, 매핑 설정, preview 렌더링과 ZIP 메타데이터는
입력 해시의 일부가 아니므로 같은 입력 ID가 전체 AASX 바이트 동일성을 보장하지 않는다.
