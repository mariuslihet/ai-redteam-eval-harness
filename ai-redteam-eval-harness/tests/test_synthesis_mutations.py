import random

import pytest
from jsonschema import Draft202012Validator

from redteam_eval.models import Family, derive_seed
from redteam_eval.mutations import OPERATORS, Bandit, PromptMutator
from redteam_eval.synthesis import mint_canaries, registry, synthesize


@pytest.mark.parametrize("seed", [0, 1, 42, 2**31])
def test_suite_deterministic_and_paired(seed):
    left, right = synthesize(seed, 6), synthesize(seed, 6)
    assert left == right
    assert left != synthesize(seed + 1, 6)
    assert len({s.id for s in left}) == 36
    assert {s.family for s in left} == set(Family)
    assert {s.metadata["document_format"] for s in left} == {
        "rag",
        "json",
        "html",
        "markdown",
        "email",
        "database",
    }
    for attack, control in zip(left[::2], left[1::2], strict=True):
        assert not attack.benign and control.benign
        assert attack.pair_id == control.pair_id
        assert attack.canaries == control.canaries
        assert attack.tools == control.tools
        assert attack.document != control.document
        assert all(c not in attack.document + attack.prompt for c in attack.canaries)


def test_seeded_world_primitives():
    assert mint_canaries(random.Random(7)) == mint_canaries(random.Random(7))
    assert registry(random.Random(7)) == registry(random.Random(7))
    assert derive_seed(1, "x", 2) == derive_seed(1, "x", 2)
    assert derive_seed(1, "x", 2) != derive_seed(1, 2, "x")
    for tool in registry(random.Random(7)):
        Draft202012Validator.check_schema(tool.parameters)


def test_additional_document_formats_are_structured_and_seeded():
    import csv
    import io
    import json
    import xml.etree.ElementTree as ET

    from redteam_eval.synthesis import DOCUMENT_FORMATS

    suite = synthesize(71, len(DOCUMENT_FORMATS))
    assert suite == synthesize(71, len(DOCUMENT_FORMATS))
    assert {s.metadata["document_format"] for s in suite} == set(DOCUMENT_FORMATS)
    for scenario in suite:
        kind = scenario.metadata["document_format"]
        if kind == "xml":
            assert ET.fromstring(scenario.document).tag == "records"
        elif kind == "csv":
            rows = list(csv.DictReader(io.StringIO(scenario.document)))
            assert rows and all(row["status"] == scenario.expected_text for row in rows)
        elif kind == "tool_result":
            value = json.loads(scenario.document)
            assert value["jsonrpc"] == "2.0"
            assert value["result"]["trust"] == "external_untrusted"


@pytest.mark.parametrize("operator", OPERATORS)
def test_mutations_are_seeded_and_retain_lineage(operator, scenario):
    mutator = PromptMutator()
    a = mutator.apply(scenario.document, 4, operator)
    assert a == mutator.apply(scenario.document, 4, operator)
    assert a != mutator.apply(scenario.document, 5, operator)
    assert a.operator == operator
    if operator == "split":
        assert a.prelude and scenario.document[-40:] in a.prompt
    with pytest.raises(ValueError):
        mutator.apply("x", 1, "undefined")


def test_bandit_exploration_and_feedback():
    a, b = Bandit(7), Bandit(7)
    selected = []
    for _ in range(30):
        x, y = a.select(), b.select()
        assert x == y
        selected.append(x[0])
        reward = 1 if x[0] == "hex" else 0
        a.update(x[0], reward)
        b.update(y[0], reward)
    assert set(selected[: len(OPERATORS)]) == set(OPERATORS)
    assert a.counts["hex"] > a.counts["rot13"]
