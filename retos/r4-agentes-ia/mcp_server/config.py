import os


def env(k: str, d: str) -> str:
    return os.environ.get(k) or d


COPILOTO_DB_RO = env("COPILOTO_DB_RO", "postgresql://copiloto_ro:ro-demo@copiloto-db:5432/copiloto")
COPILOTO_DB_RW = env("COPILOTO_DB_RW", "postgresql://copiloto_rw:rw-demo@copiloto-db:5432/copiloto")
ROUTER_DB_RO = env("ROUTER_DB_RO", "postgresql://router:router@router-db:5432/router")
OPERATOR_URL = env("OPERATOR_URL", "http://payment-operator:8080")
PARTICIPANT_URL = env("PARTICIPANT_URL", "http://participant-payment-manager:8080")
FACTURACION_URL = env("FACTURACION_URL", "http://facturacion:8080")
VERIFIER_URL = env("VERIFIER_URL", "http://invoice-verifier:8090")
HTTP_TIMEOUT = float(env("HTTP_TIMEOUT", "10"))
