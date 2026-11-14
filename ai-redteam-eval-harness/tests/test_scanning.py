import base64
import codecs
from urllib.parse import quote

import pytest

from redteam_eval.scanning import CanaryScanner, decoded_views, distance, entropy


@pytest.mark.parametrize(
    "encoding",
    [
        "plain",
        "base64",
        "hex",
        "url",
        "rot13",
        "nested",
        "zero_width",
        "separated",
        "html_entities",
        "unicode_escape",
        "reverse",
    ],
)
def test_all_synthetic_canaries_detected(encoding, scenario):
    scanner = CanaryScanner()
    for secret in scenario.canaries:
        value = {
            "plain": secret,
            "base64": base64.b64encode(secret.encode()).decode(),
            "hex": secret.encode().hex(),
            "url": quote(secret, safe=""),
            "rot13": codecs.encode(secret, "rot_13"),
            "nested": base64.b64encode(secret.encode().hex().encode()).decode(),
            "zero_width": "\u200b".join(secret),
            "separated": " ".join(secret),
            "html_entities": "".join(f"&#{ord(c)};" for c in secret),
            "unicode_escape": "".join(f"\\u{ord(c):04x}" for c in secret),
            "reverse": secret[::-1],
        }[encoding]
        assert scanner.scan(f"Record:\n{value}\nEnd", [secret]), (encoding, secret)


def test_partial_fuzzy_and_no_unrelated_false_positive(scenario):
    secret = scenario.canaries[0]
    scanner = CanaryScanner()
    assert "partial" in scanner.scan(secret[15:50], [secret])[0].method
    altered = secret[:19] + "!" + secret[20:]
    assert scanner.scan(altered, [secret])
    assert scanner.scan("ordinary public status secret system admin", scenario.canaries) == []
    assert distance("credential", "credentia1") == 1
    assert distance("abc", "abcdefghijklmnopqrstuvwxyz") > 2
    assert entropy("") == 0
    assert entropy("aaaa") == 0
    assert entropy("abcdefgh") == 3


def test_decoding_work_is_bounded():
    views = decoded_views("A" * 500000)
    assert len(views) <= 96
    assert all(len(v) <= 131072 for v, _ in views)


def test_long_output_tail_and_chunk_boundary_are_scanned(scenario):
    secret = scenario.canaries[0]
    text = "public " * 40000 + secret
    assert CanaryScanner().scan(text, [secret])
    text = " " * 65530 + base64.b64encode(secret.encode()).decode() + " " * 150000
    assert CanaryScanner().scan(text, [secret])
