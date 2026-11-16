import re

_NIC = re.compile(r"^NIC-\d{7}$")
_FAC = re.compile(r"^FAC-\d{4}-\d{2}-\d{7}$")
_MED = re.compile(r"^AMI-(cloud|onprem)-\d{4}$")
_PART = re.compile(r"^[a-z0-9-]{1,64}$")


def _check(rx, v, nombre):
    if not isinstance(v, str) or not rx.match(v):
        raise ValueError(f"{nombre} inválido")
    return v


def nic(v): return _check(_NIC, v, "nic")
def factura(v): return _check(_FAC, v, "id de factura")
def medidor(v): return _check(_MED, v, "medidor")
def participante(v): return _check(_PART, v, "participante")


def enmascarar_nic(v: str) -> str:
    return v[:4] + "***" + v[-2:] if v and len(v) > 6 else "***"
