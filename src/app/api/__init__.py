from src.app.api.health import ns as health_ns
from src.app.api.v1.account import ns as account_ns
from src.app.api.v1.app_compat import ns as app_compat_ns
from src.app.api.v1.benefit import ns as benefit_ns
from src.app.api.v1.auth import ns as auth_ns
from src.app.api.v1.enrol import ns as enrol_ns
from src.app.api.v1.hip import ns as hip_ns
from src.app.api.v1.hips import ns as hips_ns
from src.app.api.v1.login import ns as login_ns
from src.app.api.v1.profile import ns as profile_ns
from src.app.api.v1.search import ns as search_ns
from src.app.api.v1.share import ns as share_ns

# (namespace, path). Add new namespaces here; they all show up in Swagger.
NAMESPACES = [
    (health_ns, "/"),
    (auth_ns, "/api/v1/auth"),
    (enrol_ns, "/api/v1/abha/enrol"),
    (search_ns, "/api/v1/abha/search"),
    (login_ns, "/api/v1/abha/login"),
    (profile_ns, "/api/v1/abha"),
    (account_ns, "/api/v1/abha/account"),
    (benefit_ns, "/api/v1/abha/benefit"),
    (app_compat_ns, "/api/v1/abha/app"),
    (hip_ns, "/api/v1/hip"),
    (hips_ns, "/api/v1/hips"),
    (share_ns, "/api/v1/share"),
]


def register_namespaces(api):
    registered = {ns.name for ns in api.namespaces}
    for ns, path in NAMESPACES:
        if ns.name not in registered:
            api.add_namespace(ns, path=path)
