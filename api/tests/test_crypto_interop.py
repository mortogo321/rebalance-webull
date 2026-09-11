"""Cross-runtime check: Deno writes the envelope, Python reads it, and back.

The edge function encrypts credentials and the engine decrypts them. If the two
implementations ever disagree about IV length, tag placement, base64 alphabet or
AAD, every stored credential becomes unreadable -- and it would surface in
production, not in either project's own unit tests. So the contract is tested
against the real other runtime rather than against a copied-in constant.

Skips when Deno is absent (a bare `pytest` on a laptop); CI installs Deno, so
the check is enforced there. See .github/workflows/ci.yml.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.crypto import decrypt, encrypt, load_key

USER_ID = "a0000000-0000-4000-8000-000000000001"
SECRET = "wb_uat_key_11223344"
KEY_B64 = base64.b64encode(bytes(range(32))).decode()

CRYPTO_TS = (
    Path(__file__).resolve().parents[2] / "supabase" / "functions" / "_shared" / "crypto.ts"
)

pytestmark = pytest.mark.skipif(
    shutil.which("deno") is None or not CRYPTO_TS.exists(),
    reason="deno runtime or supabase/functions/_shared/crypto.ts not available",
)


def _run_deno(script: str) -> dict:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["deno", "eval", "--quiet", script],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"deno failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def test_python_decrypts_what_deno_encrypted():
    script = f"""
    import {{ encryptSecret }} from "file://{CRYPTO_TS}";
    const envelope = await encryptSecret({SECRET!r}, {{
      key: {KEY_B64!r}, aad: {USER_ID!r},
    }});
    console.log(JSON.stringify({{ envelope }}));
    """
    envelope = _run_deno(script)["envelope"]
    assert decrypt(envelope, key=load_key(KEY_B64), aad=USER_ID) == SECRET


def test_deno_decrypts_what_python_encrypted():
    envelope = encrypt(SECRET, key=load_key(KEY_B64), aad=USER_ID)
    script = f"""
    import {{ decryptSecret }} from "file://{CRYPTO_TS}";
    const plaintext = await decryptSecret({envelope!r}, {{
      key: {KEY_B64!r}, aad: {USER_ID!r},
    }});
    console.log(JSON.stringify({{ plaintext }}));
    """
    assert _run_deno(script)["plaintext"] == SECRET


def test_deno_also_refuses_another_users_aad():
    envelope = encrypt(SECRET, key=load_key(KEY_B64), aad=USER_ID)
    script = f"""
    import {{ decryptSecret }} from "file://{CRYPTO_TS}";
    let failed = false;
    try {{
      await decryptSecret({envelope!r}, {{
        key: {KEY_B64!r}, aad: "a0000000-0000-4000-8000-000000000002",
      }});
    }} catch {{ failed = true; }}
    console.log(JSON.stringify({{ failed }}));
    """
    assert _run_deno(script)["failed"] is True
