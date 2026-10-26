import pytest

from src.app.callbacks.routing import normalize_path, resolve_hip_id
from src.app.errors import ValidationError


@pytest.mark.parametrize("subpath,expected", [
    ("v3/hip/patient/share", "api/v3/hip/patient/share"),
    ("/v3/hip/token/on-generate-token/", "api/v3/hip/token/on-generate-token"),
    ("v3/hiu/consent/request/on-init", "api/v3/hiu/consent/request/on-init"),
])
def test_normalize_path(subpath, expected):
    assert normalize_path(subpath) == expected


@pytest.mark.parametrize("subpath", [
    "", "v3/../secret", "v3//double", "v3/./x", "v3/has space", "v3/%2e%2e/x", "v3/semi;colon", "x" * 300,
    "..", "v3/hip?x=1",
])
def test_normalize_path_rejects_odd_paths(subpath):
    with pytest.raises(ValidationError) as exc:
        normalize_path(subpath)
    assert exc.value.code == "INVALID_CALLBACK_PATH"


def test_header_wins_over_body():
    body = {"metaData": {"hipId": "FROM-BODY"}}
    assert resolve_hip_id({"X-HIP-ID": "FROM-HEADER"}, body) == "FROM-HEADER"


@pytest.mark.parametrize("body", [
    {"metaData": {"hipId": "IN001"}},
    {"hip": {"id": "IN001"}},
    {"hip": {"hipId": "IN001"}},
    {"hipId": "IN001"},
    {"notification": {"consentDetail": {"hip": {"id": "IN001"}}}},
    {"consentDetail": {"hip": {"id": "IN001"}}},
])
def test_hip_id_found_in_every_known_body_place(body):
    assert resolve_hip_id({}, body) == "IN001"


@pytest.mark.parametrize("body", [
    {}, {"metaData": {}}, {"metaData": "text"}, {"hipId": ""}, {"hipId": "   "}, {"hipId": 5}, {"hip": None},
])
def test_hip_id_missing(body):
    assert resolve_hip_id({}, body) is None


def test_hip_id_is_trimmed_and_capped_at_64_chars():
    assert resolve_hip_id({"X-HIP-ID": "  IN001  "}, {}) == "IN001"
    assert len(resolve_hip_id({"X-HIP-ID": "A" * 200}, {})) == 64
    assert len(resolve_hip_id({}, {"hipId": "B" * 200})) == 64
