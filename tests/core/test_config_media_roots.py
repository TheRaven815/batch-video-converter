from __future__ import annotations

from pathlib import Path

from video_converter.core.config import _derive_key_from_label, _parse_media_roots


# ---------------------------------------------------------------------------
# _derive_key_from_label
# ---------------------------------------------------------------------------


def test_derive_key_lowercases_simple_label() -> None:
    used: set[str] = set()
    assert _derive_key_from_label("Filmler", used) == "filmler"


def test_derive_key_replaces_special_chars_with_underscores() -> None:
    used: set[str] = set()
    assert _derive_key_from_label("4K Movies!", used) == "4k_movies"


def test_derive_key_strips_leading_trailing_underscores() -> None:
    used: set[str] = set()
    assert _derive_key_from_label("--Filmler--", used) == "filmler"


def test_derive_key_falls_back_to_root_for_empty_label() -> None:
    used: set[str] = set()
    assert _derive_key_from_label("", used) == "root"

    used2: set[str] = set()
    assert _derive_key_from_label("   ", used2) == "root"


def test_derive_key_disambiguates_duplicates() -> None:
    used: set[str] = set()
    first = _derive_key_from_label("Filmler", used)
    second = _derive_key_from_label("Filmler", used)
    third = _derive_key_from_label("Filmler", used)

    assert first == "filmler"
    assert second == "filmler_2"
    assert third == "filmler_3"


def test_derive_key_does_not_collide_with_existing_keys() -> None:
    used: set[str] = {"filmler"}
    assert _derive_key_from_label("Filmler", used) == "filmler_2"


# ---------------------------------------------------------------------------
# _parse_media_roots — key stability
# ---------------------------------------------------------------------------


def test_parse_media_roots_uses_label_as_key(tmp_path: Path) -> None:
    raw = "Filmler=/media/filmler;Diziler=/media/diziler"
    roots = _parse_media_roots(raw, input_dir=tmp_path / "input")

    keys = [r.key for r in roots]
    labels = [r.label for r in roots]

    assert keys == ["filmler", "diziler"]
    assert labels == ["Filmler", "Diziler"]


def test_parse_media_roots_key_stable_regardless_of_order(tmp_path: Path) -> None:
    raw_a = "Filmler=/a;Diziler=/b"
    raw_b = "Diziler=/b;Filmler=/a"

    roots_a = _parse_media_roots(raw_a, input_dir=tmp_path / "input")
    roots_b = _parse_media_roots(raw_b, input_dir=tmp_path / "input")

    keys_a = [r.key for r in roots_a]
    keys_b = [r.key for r in roots_b]

    # Both should contain the same keys (order may differ because mount order differs)
    assert set(keys_a) == set(keys_b) == {"filmler", "diziler"}


def test_parse_media_roots_duplicate_labels_disambiguated(tmp_path: Path) -> None:
    raw = "Filmler=/a;Filmler=/b"
    roots = _parse_media_roots(raw, input_dir=tmp_path / "input")

    assert roots[0].key == "filmler"
    assert roots[1].key == "filmler_2"


def test_parse_media_roots_no_label_gets_default_key(tmp_path: Path) -> None:
    raw = "/media/unnamed"
    roots = _parse_media_roots(raw, input_dir=tmp_path / "input")

    assert len(roots) == 1
    assert roots[0].key == "root_1"
    assert roots[0].label == "Root 1"


def test_parse_media_roots_empty_returns_input_fallback(tmp_path: Path) -> None:
    roots = _parse_media_roots("", input_dir=tmp_path / "input")
    assert len(roots) == 1
    assert roots[0].key == "input"
    assert roots[0].label == "Input"
