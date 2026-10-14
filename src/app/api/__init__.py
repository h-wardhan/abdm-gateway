from src.app.api.health import ns as health_ns
from src.app.api.v1.auth import ns as auth_ns
from src.app.api.v1.enrol import ns as enrol_ns
from src.app.api.v1.login import ns as login_ns
from src.app.api.v1.profile import ns as profile_ns
from src.app.api.v1.search import ns as search_ns

# (namespace, path). Add new namespaces here; they all show up in Swagger.
NAMESPACES = [
    (health_ns, "/"),
    (auth_ns, "/api/v1/auth"),
    (enrol_ns, "/api/v1/abha/enrol"),
    (search_ns, "/api/v1/abha/search"),
    (login_ns, "/api/v1/abha/login"),
    (profile_ns, "/api/v1/abha"),
]


def register_namespaces(api):
    registered = {ns.name for ns in api.namespaces}
    for ns, path in NAMESPACES:
        if ns.name not in registered:
            api.add_namespace(ns, path=path)
