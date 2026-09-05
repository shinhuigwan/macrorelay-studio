"""OCR 결과 비교·보정 모듈.

PaddleOCR과 Tesseract 결과를 비교하여 최적 결과를 선택하고,
유사 문자 보정, 정규식 매칭, 기대 문자열 힌트를 적용한다.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "OcrBox",
    "OcrCandidate",
    "OcrFinalResult",
    "merge_results",
    "correct_similar_chars",
    "match_text",
    "normalize_text",
    "select_best_box",
    "extract_regex_value",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class OcrBox:
    """인식된 텍스트 하나의 위치와 신뢰도."""

    text: str
    confidence: float
    rect: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    center: tuple[int, int]
    engine: str = ""


@dataclass
class OcrCandidate:
    """하나의 OCR 엔진 결과."""

    text: str
    normalized_text: str
    confidence: float
    boxes: list[OcrBox]
    engine: str
    elapsed_ms: float
    profile: str = ""


@dataclass
class OcrFinalResult:
    """최종 OCR 결과."""

    success: bool
    text: str
    normalized_text: str
    confidence: float
    boxes: list[OcrBox]
    engine: str
    profile: str
    elapsed_ms: float
    candidates: list[OcrCandidate] = field(default_factory=list)
    match_found: bool = False
    match_text: str = ""
    match_box: OcrBox | None = None
    extracted_number: float | None = None
    debug_info: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 유사 문자 보정 테이블
# ---------------------------------------------------------------------------

SIMILAR_CHARS: dict[str, str] = {
    # 숫자 ↔ 영문
    "0": "O",
    "O": "0",
    "o": "0",
    "1": "l",
    "l": "1",
    "I": "1",
    "|": "1",
    "!": "1",
    "5": "S",
    "S": "5",
    "s": "5",
    "8": "B",
    "B": "8",
    "6": "G",
    "G": "6",
    "2": "Z",
    "Z": "2",
    "z": "2",
    # 한글 ↔ 영문/숫자
    "ㅇ": "O",
    "ㅁ": "□",
    "ㄱ": "7",
    # 특수문자 혼동
    ",": ".",
    "'": "'",
    "'": "'",
    """: '"',
    """: '"',
    "–": "-",
    "—": "-",
    "…": "...",
}

# 숫자 유사 문자 보정 (숫자 기대 시)
NUMBER_CORRECTIONS: dict[str, str] = {
    "O": "0",
    "o": "0",
    "ㅇ": "0",
    "l": "1",
    "I": "1",
    "|": "1",
    "!": "1",
    "S": "5",
    "s": "5",
    "B": "8",
    "G": "6",
    "g": "9",
    "Z": "2",
    "z": "2",
    "b": "6",
    "q": "9",
    "D": "0",
    "T": "7",
    ",": "",
    " ": "",
}


# ---------------------------------------------------------------------------
# 텍스트 정규화
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """OCR 결과 텍스트를 정규화한다."""
    if not text:
        return ""
    # Unicode 정규화 (NFC)
    text = unicodedata.normalize("NFC", text)
    # 화면 OCR에 끼어드는 폭 없는 문자·제어 문자는 검색을 방해한다.
    text = "".join(
        ch for ch in text
        if ch in "\n\t" or (unicodedata.category(ch) not in {"Cf", "Cc"})
    )
    # 줄바꿈 통일
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned = [line.strip() for line in lines if line.strip()]
    return "\n".join(cleaned)


# ---------------------------------------------------------------------------
# 유사 문자 보정
# ---------------------------------------------------------------------------


def correct_similar_chars(
    text: str,
    expect_text: str = "",
    whitelist: str = "",
    is_number: bool = False,
) -> str:
    """유사 문자를 보정한다.

    Args:
        text: 원본 인식 텍스트
        expect_text: 기대 문자열 (힌트)
        whitelist: 허용 문자 목록
        is_number: 숫자 전용 모드
    """
    if not text:
        return text

    # 숫자 전용 모드
    if is_number:
        result = []
        for ch in text:
            if ch.isdigit() or ch in ".-+":
                result.append(ch)
            elif ch in NUMBER_CORRECTIONS:
                result.append(NUMBER_CORRECTIONS[ch])
            # 그 외 문자는 제거
        return "".join(result)

    # 화이트리스트 모드
    if whitelist:
        whitelist_set = set(whitelist)
        result = []
        for ch in text:
            if ch in whitelist_set:
                result.append(ch)
            elif ch in SIMILAR_CHARS and SIMILAR_CHARS[ch] in whitelist_set:
                result.append(SIMILAR_CHARS[ch])
            # 화이트리스트에 없는 문자는 제거
        return "".join(result)

    # 기대 문자열 힌트 기반 보정
    if expect_text and len(text) == len(expect_text):
        result = []
        for i, (actual, expected) in enumerate(zip(text, expect_text)):
            if actual == expected:
                result.append(actual)
            elif actual in SIMILAR_CHARS and SIMILAR_CHARS[actual] == expected:
                result.append(expected)
            else:
                result.append(actual)
        return "".join(result)

    return text


# ---------------------------------------------------------------------------
# 텍스트 매칭
# ---------------------------------------------------------------------------


def match_text(
    text: str,
    find_text: str,
    mode: str = "contains",
    case_sensitive: bool = False,
) -> bool:
    """텍스트 매칭을 수행한다.

    Args:
        text: 검색 대상 텍스트
        find_text: 찾을 텍스트
        mode: 매칭 모드 (contains, exact, regex, starts_with, ends_with)
        case_sensitive: 대소문자 구분
    """
    if not find_text:
        return bool(text.strip())

    t = text if case_sensitive else text.lower()
    f = find_text if case_sensitive else find_text.lower()

    if mode == "exact":
        return t.strip() == f.strip()
    elif mode == "starts_with":
        return t.strip().startswith(f.strip())
    elif mode == "ends_with":
        return t.strip().endswith(f.strip())
    elif mode == "regex":
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            return bool(re.search(find_text, text, flags))
        except re.error:
            logger.warning("정규식 오류: %s", find_text)
            return False
    else:  # contains
        return f.strip() in t


def find_text_in_boxes(
    boxes: list[OcrBox],
    find_text: str,
    mode: str = "contains",
    case_sensitive: bool = False,
    position_priority: str = "top_left",
) -> OcrBox | None:
    """박스 목록에서 특정 텍스트를 포함하는 박스를 찾는다.

    Args:
        boxes: OCR 박스 목록
        find_text: 찾을 텍스트
        mode: 매칭 모드
        case_sensitive: 대소문자 구분
        position_priority: 위치 우선순위 (top_left, confidence, first)

    Returns:
        매칭된 박스 또는 None
    """
    matched: list[OcrBox] = []

    for box in boxes:
        if match_text(box.text, find_text, mode, case_sensitive):
            matched.append(box)

    if not matched:
        return None

    if len(matched) == 1:
        return matched[0]

    if position_priority == "confidence":
        matched.sort(key=lambda b: b.confidence, reverse=True)
    elif position_priority == "top_left":
        matched.sort(key=lambda b: (b.rect[1], b.rect[0]))
    elif position_priority == "top_right":
        matched.sort(key=lambda b: (b.rect[1], -b.rect[2]))
    elif position_priority == "bottom_left":
        matched.sort(key=lambda b: (-b.rect[3], b.rect[0]))
    elif position_priority == "largest":
        matched.sort(
            key=lambda b: (b.rect[2] - b.rect[0]) * (b.rect[3] - b.rect[1]),
            reverse=True,
        )
    # "first" → 순서 유지

    return matched[0]


# ---------------------------------------------------------------------------
# 숫자 추출
# ---------------------------------------------------------------------------


def extract_number(text: str) -> float | None:
    """텍스트에서 숫자를 추출한다."""
    if not text:
        return None

    # 숫자 유사 문자 보정 후 추출
    corrected = correct_similar_chars(text, is_number=True)

    # 숫자 패턴 찾기 (소수, 음수, 콤마 포함)
    match = re.search(r"[+-]?\d+(?:\.\d+)?", corrected)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def extract_regex_value(text: str, pattern: str, group: int = 1) -> str | None:
    """Return one capture group from OCR text, or ``None`` when it does not match."""
    if not pattern:
        return None
    try:
        match = re.search(pattern, text or "")
    except re.error:
        return None
    if match is None:
        return None
    try:
        value = match.group(max(0, int(group)))
    except (IndexError, TypeError, ValueError):
        return None
    return str(value).strip()


def check_number_condition(
    value: float | None,
    condition: str,
    target: float,
) -> bool:
    """숫자 조건을 확인한다.

    Args:
        value: 추출된 숫자
        condition: 조건 (gte, lte, gt, lt, eq, neq, range)
        target: 비교 대상 값
    """
    if value is None:
        return False

    if condition == "gte":
        return value >= target
    elif condition == "lte":
        return value <= target
    elif condition == "gt":
        return value > target
    elif condition == "lt":
        return value < target
    elif condition == "eq":
        return abs(value - target) < 1e-9
    elif condition == "neq":
        return abs(value - target) >= 1e-9
    else:
        return False


# ---------------------------------------------------------------------------
# 결과 선택·병합
# ---------------------------------------------------------------------------


def select_best_box(
    boxes: list[OcrBox],
    position_priority: str = "confidence",
) -> OcrBox | None:
    """여러 박스 중 최적의 박스를 선택한다."""
    if not boxes:
        return None

    if position_priority == "confidence":
        return max(boxes, key=lambda b: b.confidence)
    elif position_priority == "top_left":
        return min(boxes, key=lambda b: (b.rect[1], b.rect[0]))
    elif position_priority == "top_right":
        return min(boxes, key=lambda b: (b.rect[1], -b.rect[2]))
    elif position_priority == "bottom_left":
        return min(boxes, key=lambda b: (-b.rect[3], b.rect[0]))
    elif position_priority == "largest":
        return max(
            boxes,
            key=lambda b: (b.rect[2] - b.rect[0]) * (b.rect[3] - b.rect[1]),
        )
    else:
        return boxes[0]


def merge_results(
    candidates: list[OcrCandidate],
    expect_text: str = "",
    regex: str = "",
    whitelist: str = "",
    find_text: str = "",
    match_mode: str = "contains",
    is_number: bool = False,
    number_condition: str = "",
    number_value: float = 0.0,
    minimum_confidence: float = 0.0,
    position_priority: str = "top_left",
    lang: str = "eng+kor",
) -> OcrFinalResult:
    """여러 OCR 엔진 결과를 비교하여 최적 결과를 선택한다.

    Args:
        candidates: 각 엔진의 결과 목록
        expect_text: 기대 문자열 힌트
        regex: 정규식 패턴
        whitelist: 허용 문자 목록
        find_text: 찾을 텍스트
        match_mode: 매칭 모드
        is_number: 숫자 전용 모드
        number_condition: 숫자 조건
        number_value: 숫자 비교 대상 값

    Returns:
        최종 OCR 결과
    """
    if not candidates:
        return OcrFinalResult(
            success=False,
            text="",
            normalized_text="",
            confidence=0.0,
            boxes=[],
            engine="none",
            profile="",
            elapsed_ms=0.0,
        )

    # 유효한 후보 필터링
    valid = [c for c in candidates if c.text.strip()]

    if not valid:
        # 모든 후보가 빈 결과면 첫 번째 반환
        best = candidates[0]
        return OcrFinalResult(
            success=False,
            text="",
            normalized_text="",
            confidence=0.0,
            boxes=[],
            engine=best.engine,
            profile=best.profile,
            elapsed_ms=sum(c.elapsed_ms for c in candidates),
            candidates=candidates,
        )

    # 1) 기대 문자열 힌트가 있으면 가장 유사한 결과 선택
    if expect_text:
        scored = []
        for c in valid:
            corrected = correct_similar_chars(c.text, expect_text=expect_text)
            similarity = _text_similarity(corrected, expect_text)
            scored.append((c, similarity, corrected))
        scored.sort(key=lambda x: (-x[1], -x[0].confidence))
        best_candidate, _, corrected_text = scored[0]
    # 2) 찾을 문자열을 실제로 포함한 엔진을 먼저 선택한다.
    elif find_text:
        matched = [c for c in valid if match_text(c.text, find_text, match_mode)]
        pool = matched or valid
        best_candidate = max(pool, key=lambda c: _candidate_score(c, valid, lang))
        corrected_text = correct_similar_chars(best_candidate.text, whitelist=whitelist)
    # 3) 정규식이 있으면 매칭되는 결과 선택
    elif regex:
        regex_matched = []
        for c in valid:
            try:
                if re.search(regex, c.text):
                    regex_matched.append(c)
            except re.error:
                pass
        if regex_matched:
            best_candidate = max(regex_matched, key=lambda c: _candidate_score(c, regex_matched, lang))
        else:
            best_candidate = max(valid, key=lambda c: _candidate_score(c, valid, lang))
        corrected_text = correct_similar_chars(
            best_candidate.text, whitelist=whitelist
        )
    else:
        # 4) 엔진마다 신뢰도 척도가 다르므로 언어 품질과 검출 범위를 함께 본다.
        best_candidate = max(valid, key=lambda c: _candidate_score(c, valid, lang))
        corrected_text = correct_similar_chars(
            best_candidate.text, whitelist=whitelist, is_number=is_number
        )

    # 텍스트 정규화
    normalized = normalize_text(corrected_text)

    # 전체 소요 시간
    total_elapsed = sum(c.elapsed_ms for c in candidates)

    # 박스 정보 (보정된 텍스트 반영)
    boxes = [
        OcrBox(
            text=correct_similar_chars(b.text, whitelist=whitelist, is_number=is_number),
            confidence=b.confidence,
            rect=b.rect,
            center=b.center,
            engine=best_candidate.engine,
        )
        for b in best_candidate.boxes
        if b.confidence >= minimum_confidence
    ]

    # 텍스트 찾기
    match_found = False
    match_box: OcrBox | None = None
    matched_text = ""
    if find_text:
        match_found = match_text(normalized, find_text, match_mode)
        if match_found:
            matched_text = find_text
            match_box = find_text_in_boxes(
                boxes, find_text, match_mode, position_priority=position_priority
            )

    # 숫자 추출 및 조건
    extracted_num: float | None = None
    if is_number:
        extracted_num = extract_number(normalized)
        if number_condition and extracted_num is not None:
            condition_met = check_number_condition(
                extracted_num, number_condition, number_value
            )
            if not condition_met:
                match_found = False

    # 성공 판정
    success = bool(normalized.strip()) and best_candidate.confidence >= minimum_confidence
    if find_text:
        success = match_found
    if is_number and number_condition:
        success = (
            extracted_num is not None
            and check_number_condition(extracted_num, number_condition, number_value)
        )

    return OcrFinalResult(
        success=success,
        text=corrected_text,
        normalized_text=normalized,
        confidence=best_candidate.confidence,
        boxes=boxes,
        engine=best_candidate.engine,
        profile=best_candidate.profile,
        elapsed_ms=round(total_elapsed, 2),
        candidates=candidates,
        match_found=match_found,
        match_text=matched_text,
        match_box=match_box,
        extracted_number=extracted_num,
    )


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _text_similarity(a: str, b: str) -> float:
    """두 문자열의 유사도를 반환한다 (0.0~1.0)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    a_lower = a.strip().lower()
    b_lower = b.strip().lower()

    if a_lower == b_lower:
        return 1.0

    # 레벤슈타인 거리 기반 유사도
    max_len = max(len(a_lower), len(b_lower))
    if max_len == 0:
        return 1.0

    distance = _levenshtein_distance(a_lower, b_lower)
    return 1.0 - (distance / max_len)


def _candidate_score(candidate: OcrCandidate, pool: list[OcrCandidate], lang: str) -> float:
    """서로 다른 OCR 엔진의 신뢰도를 비교 가능한 점수로 보정한다."""
    normalized = normalize_text(candidate.normalized_text or candidate.text)
    meaningful = [ch for ch in normalized if not ch.isspace()]
    max_length = max(
        (len([ch for ch in normalize_text(c.normalized_text or c.text) if not ch.isspace()]) for c in pool),
        default=1,
    )
    coverage = min(len(meaningful) / max(max_length, 1), 1.0)
    invalid = 0
    compatibility_jamo = 0
    for ch in meaningful:
        category = unicodedata.category(ch)
        if ch == "\ufffd" or category in {"Co", "Cs", "Cn"}:
            invalid += 1
        if "kor" in str(lang).casefold() and ("\u3130" <= ch <= "\u318f" or "\u1100" <= ch <= "\u11ff"):
            compatibility_jamo += 1
    quality = 1.0 - ((invalid + compatibility_jamo * 0.6) / max(len(meaningful), 1))
    confidence = max(0.0, min(float(candidate.confidence), 1.0))
    return confidence * 0.55 + max(quality, 0.0) * 0.25 + coverage * 0.20


def _levenshtein_distance(s1: str, s2: str) -> int:
    """레벤슈타인 편집 거리를 계산한다."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]
