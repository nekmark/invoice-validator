from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
from pathlib import Path

import pytest

from pipeline import naming


@pytest.mark.parametrize(
    "stem, expected",
    [
        ("Jane Doe_2026-09", "2026-09"),
        ("janedoe-2026-09", "2026-09"),
        ("Jane_Doe_2026_09", "2026-09"),
        ("janedoe-01-2026-09", "2026-09"),
        ("janedoe-2026-09-001", "2026-09"),
        ("janedoe-2026-0010", ""),      # invoice number, not a month
        ("Jane Doe_FV20260010", ""),
        ("Jane Doe_INV-009", ""),
        ("Jane_Doe_260901", ""),        # ambiguous short form: don't guess
        ("janedoe-2026-13", ""),
    ],
)
def test_suggest_month(stem, expected):
    assert naming.suggest_month(stem) == expected


def test_suggest_contractor_prefers_known_name():
    assert naming.suggest_contractor("janedoe-2026-09", ["Jane Doe", "John Roe"]) == "Jane Doe"
    assert naming.suggest_contractor("Jána_Dóe_invoice_2026", ["Jana Doe"]) == "Jana Doe"


def test_suggest_contractor_fallback_tidies_lead_text():
    assert naming.suggest_contractor("Jane Doe_invoice_20260009", []) == "Jane Doe"
    assert naming.suggest_contractor("jane_doe-2026-09", []) == "Jane Doe"


def test_renamed_file_follows_convention(tmp_path):
    src = tmp_path / "janedoe-2026-0010.pdf"
    src.write_bytes(b"%PDF-fake")
    assert not naming.follows_convention(src)
    out = naming.rename_to_convention(src, "Jane Doe", "2026-09")
    assert out.name == "Jane Doe_2026-09.pdf" and out.exists() and not src.exists()
    assert naming.follows_convention(out)


def test_rename_never_overwrites(tmp_path):
    (tmp_path / "Jane Doe_2026-09.pdf").write_bytes(b"existing")
    src = tmp_path / "other.pdf"
    src.write_bytes(b"new")
    with pytest.raises(FileExistsError):
        naming.rename_to_convention(src, "Jane Doe", "2026-09")
    assert (tmp_path / "Jane Doe_2026-09.pdf").read_bytes() == b"existing"


@pytest.mark.parametrize("contractor, month", [("", "2026-09"), ("Jane", "2026-9"), ("Jane", "09-2026"), ("Ja/ne", "2026-09"), ("Jane 2", "2026-09")])
def test_bad_choices_rejected(contractor, month):
    assert naming.validate_choice(contractor, month) is not None


def test_decomposed_accents_match_typed_name(tmp_path):
    import unicodedata
    from pipeline import db, extraction
    nfd = unicodedata.normalize("NFD", "Émile Dupont")
    name, _, _ = extraction.parse_filename(Path(f"{nfd}_2026-09.pdf"))
    assert db.upsert_contractor(name) == db.upsert_contractor("Émile Dupont")


def test_unaccented_name_maps_to_existing_contractor():
    from pipeline import db
    cid = db.upsert_contractor("Émile Dupont")
    assert db.upsert_contractor("Emile Dupont") == cid
    assert db.upsert_contractor("  emile  DUPONT ") == cid
    assert db.upsert_contractor("Émilie Dupont") != cid


def test_surname_first_maps_to_existing_contractor():
    from pipeline import db
    cid = db.upsert_contractor("Zoë Lindqvist")
    assert db.upsert_contractor("Lindqvist Zoe") == cid
    assert db.upsert_contractor("Zoe Lindqvist") == cid


def test_surname_sort_ignores_accents_and_case():
    names = ["Emily Nolan", "Olivia Carter", "Jack Brooks", "Ava Sánchez", "Noah Sullivan",
             "Adam Walker", "Emma Young", "Ryan Reyes", "Lea Rivera", "Zoe Abbott", "liam sanders"]
    ordered = sorted(names, key=naming.surname_sort_key)
    assert [n.split()[-1] for n in ordered] == [
        "Abbott", "Brooks", "Carter", "Nolan", "Reyes", "Rivera", "Sánchez", "sanders", "Sullivan", "Walker", "Young",
    ]


def test_surname_sort_ties_broken_by_first_name():
    assert sorted(["Grace Miller", "Anna Miller"], key=naming.surname_sort_key) == ["Anna Miller", "Grace Miller"]



def test_display_name_puts_surname_first():
    assert naming.display_name("Émile Dupont") == "Dupont Émile"
    assert naming.display_name("Anna Marie Nová") == "Nová Anna Marie"
    assert naming.display_name("Cher") == "Cher"
    assert naming.display_name("") == ""
