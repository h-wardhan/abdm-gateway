import jwt as pyjwt
import pytest
import responses
from cryptography.hazmat.primitives.asymmetric import rsa

from src.app.callbacks import auth
from src.app.errors import AuthError
from src.app.extensions import cache
from tests.conftest import JWT_KID, jwks_body, make_jwt


def _verify_reason(token):
    with pytest.raises(auth.CallbackAuthError) as exc:
        auth.verify(token)
    return exc.value.reason


def test_valid_token(app, jwks):
    claims = auth.verify(f"Bearer {make_jwt(sub='abdm')}")
    assert claims["sub"] == "abdm"


def test_token_without_bearer_prefix(app, jwks):
    assert auth.verify(make_jwt())


@pytest.mark.parametrize("header", [None, "", "   ", "Bearer ", "bearer   "])
def test_missing_token(app, jwks, header):
    assert _verify_reason(header) == "missing_token"


def test_malformed_token(app, jwks):
    assert _verify_reason("Bearer not-a-jwt") == "malformed_token"


def test_expired_token(app, jwks):
    assert _verify_reason(make_jwt(expires_in=-60)) == "expired"


def test_token_without_exp_is_rejected(app, jwks):
    token = pyjwt.encode({"iss": "x"}, __import__("tests.conftest", fromlist=["_JWT_KEY"])._JWT_KEY,
                         algorithm="RS256", headers={"kid": JWT_KID})
    assert _verify_reason(token) == "invalid_token"


def test_signed_with_another_key(app, jwks):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert _verify_reason(make_jwt(key=other)) == "invalid_token"


def test_wrong_algorithm_is_rejected(app, jwks):
    token = pyjwt.encode({"exp": 9999999999}, "secret", algorithm="HS256", headers={"kid": JWT_KID})
    assert _verify_reason(token) == "invalid_token"


def test_broken_jwk_is_invalid_token(app, http):
    http.add(responses.GET, app.config["ABDM_JWKS_URL"],
             json={"keys": [{"kid": JWT_KID, "kty": "RSA", "n": "AQAB"}]})
    assert _verify_reason(make_jwt()) == "invalid_token"


def test_unknown_kid_refreshes_keys_once_then_works(app, http):
    # first fetch has only an old key; ABDM rotated, the refetch has the new one
    http.add(responses.GET, app.config["ABDM_JWKS_URL"], json=jwks_body("old-kid"))
    http.add(responses.GET, app.config["ABDM_JWKS_URL"], json=jwks_body("old-kid", "new-kid"))
    assert auth.verify(make_jwt(kid="new-kid"))
    assert len(http.calls) == 2


def test_unknown_kid_after_refresh_is_rejected(app, http):
    http.add(responses.GET, app.config["ABDM_JWKS_URL"], json=jwks_body("old-kid"))
    assert _verify_reason(make_jwt(kid="never-seen")) == "unknown_key"
    assert len(http.calls) == 2  # fetch + one forced refresh, no more


def test_keys_are_cached(app, jwks):
    auth.verify(make_jwt())
    auth.verify(make_jwt())
    assert len(jwks.calls) == 1


@pytest.mark.parametrize("response", [
    {"status": 500, "json": {}},
    {"status": 200, "body": "not json"},
    {"status": 200, "json": {"keys": []}},
    {"status": 200, "json": {"keys": "nope"}},
    {"status": 200, "json": ["a-list"]},
])
def test_jwks_problems_reject_the_callback(app, http, response):
    http.add(responses.GET, app.config["ABDM_JWKS_URL"], **response)
    reason = _verify_reason(make_jwt())
    assert reason in ("jwks_unavailable", "jwks_empty")


def test_jwks_unreachable(app, http):
    http.add(responses.GET, app.config["ABDM_JWKS_URL"], body=__import__("requests").ConnectionError("down"))
    assert _verify_reason(make_jwt()) == "jwks_unavailable"


def test_authenticate_jwt_mode_accepts_valid(app, jwks):
    assert auth.authenticate(make_jwt()) == auth.OK


def test_authenticate_jwt_mode_rejects_invalid(app, jwks):
    with pytest.raises(AuthError) as exc:
        auth.authenticate("Bearer nope")
    assert exc.value.code == "CALLBACK_AUTH_FAILED"
    assert exc.value.status == 401


def test_authenticate_observe_mode_never_rejects(app, jwks):
    app.config["CALLBACK_AUTH_MODE"] = "observe"
    assert auth.authenticate(make_jwt()) == auth.OK
    assert auth.authenticate(None) == "failed:missing_token"
    assert auth.authenticate("Bearer nope") == "failed:malformed_token"


def test_authenticate_off_mode_skips_everything(app, http):
    app.config["CALLBACK_AUTH_MODE"] = "off"
    assert auth.authenticate(None) == auth.SKIPPED
    assert len(http.calls) == 0  # not even a JWKS fetch


def test_cache_is_used_across_calls(app, jwks):
    auth.verify(make_jwt())
    assert cache.get(auth.JWKS_CACHE_KEY)
