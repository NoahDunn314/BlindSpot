from blindspot.models import Entity
from blindspot.resolution import EntityResolver, normalize_name


def _entities():
    return [
        Entity("ent-pinecrest", "company", "Pinecrest Metal Works LLC", "PM-2044", "IN"),
        Entity("ent-pinecrest-tx", "company", "Pinecrest Metal Works LLC", "PM-9981", "TX"),
        Entity("ent-cedar", "company", "Cedar Valley Packaging Co", "CV-5520", "MI"),
    ]


def test_normalize_name_drops_one_legal_suffix_and_punctuation():
    assert normalize_name("Pinecrest Metal Works, LLC") == "pinecrest metal works"
    assert normalize_name("Ironvale Castings (Plant 2)") == "ironvale castings plant 2"


def test_registry_id_is_strongest_match():
    result = EntityResolver(_entities()).resolve({"name": "Pinecrest Metal Works LLC", "registry_id": "PM-9981"})
    assert result.entity_id == "ent-pinecrest-tx"
    assert result.method == "registry_id"


def test_unknown_registry_id_is_not_linked_even_if_name_matches():
    result = EntityResolver(_entities()).resolve({"name": "Cedar Valley Packaging Co", "registry_id": "CV-0000"})
    assert result.entity_id is None


def test_name_and_state_resolves_when_unique():
    result = EntityResolver(_entities()).resolve({"name": "Pinecrest Metal Works LLC", "state": "IN"})
    assert result.entity_id == "ent-pinecrest"
    assert result.method == "name_and_state"


def test_name_matching_two_entities_without_state_is_ambiguous_and_rejected():
    result = EntityResolver(_entities()).resolve({"name": "Pinecrest Metal Works LLC"})
    assert result.entity_id is None
    assert "ambiguous" in result.reason


def test_state_conflict_is_rejected():
    result = EntityResolver(_entities()).resolve({"name": "Cedar Valley Packaging Co", "state": "TX"})
    assert result.entity_id is None


def test_name_only_match_is_weak_but_allowed_when_unique():
    result = EntityResolver(_entities()).resolve({"name": "Cedar Valley Packaging"})
    assert result.entity_id == "ent-cedar"
    assert result.method == "name_only"
