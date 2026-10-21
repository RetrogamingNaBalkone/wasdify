"""Deterministic packaging of WASDify's own additional files."""
from io import BytesIO
import zipfile


def build_pak(payloads: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return output.getvalue()
