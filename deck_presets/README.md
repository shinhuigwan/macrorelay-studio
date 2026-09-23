# 내장 Deck Dock 구성

`macrorelay_bundled_deck.json`은 Deck Dock의 **내장 구성**입니다. 현재 트레이딩 덱의 그리드, 프리셋, 페이지, 슬롯 액션과 아이콘뿐 아니라 `매크로 실행` 슬롯이 참조하는 매크로 6개와 이미지 자산 6개를 포함합니다.

Deck Dock의 **내장 구성 불러오기** 또는 QuickSlot 환경 설정의 **백업 & 복원 → 내장 Deck Dock 구성 불러오기**에서 적용할 수 있습니다. 적용하면 현재 Deck 구성이 교체됩니다. 실행 대상 프로그램과 창 제목 등은 백업 당시 값이므로 다른 PC에서는 다시 지정해야 할 수 있습니다.

내장 파일을 현재 로컬 구성으로 갱신할 때는 프로젝트 루트에서 `python tools/build_bundled_deck_backup.py`를 실행합니다. 일반 백업 파일은 QuickSlot 환경 설정의 **백업 파일 내보내기**로 생성합니다.
