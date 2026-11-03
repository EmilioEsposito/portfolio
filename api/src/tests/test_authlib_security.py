"""Exercise the installed verifier against the October 2026 Authlib bypasses."""

import base64
import hashlib
import hmac
import unittest

from authlib.jose import JsonWebSignature
from authlib.jose.errors import JoseError


class AuthlibSecurityTests(unittest.TestCase):
    def test_empty_signature_list_is_rejected(self):
        verifier = JsonWebSignature(algorithms=["HS256"])
        for key in (None, b"local-regression-key-at-least-32-bytes"):
            with self.subTest(key_present=key is not None):
                with self.assertRaises((JoseError, ValueError)):
                    verifier.deserialize_json({"payload": "e30", "signatures": []}, key)

    def test_empty_hmac_keys_are_rejected(self):
        for algorithm in ("HS256", "HS384", "HS512"):
            verifier = JsonWebSignature(algorithms=[algorithm])
            for key in (b"", ""):
                with self.subTest(algorithm=algorithm, key_type=type(key).__name__):
                    with self.assertRaises((JoseError, ValueError)):
                        verifier.serialize_compact({"alg": algorithm}, b"fixture", key)

    def test_forged_empty_key_signatures_are_rejected(self):
        for algorithm, digest in (
            ("HS256", hashlib.sha256),
            ("HS384", hashlib.sha384),
            ("HS512", hashlib.sha512),
        ):
            header = base64.urlsafe_b64encode((f'{{"alg":"{algorithm}"}}').encode()).rstrip(b"=")
            message = header + b".e30"
            signature = base64.urlsafe_b64encode(hmac.new(b"", message, digest).digest()).rstrip(
                b"="
            )
            verifier = JsonWebSignature(algorithms=[algorithm])
            for key in (b"", ""):
                with self.subTest(algorithm=algorithm, key_type=type(key).__name__):
                    with self.assertRaises((JoseError, ValueError)):
                        verifier.deserialize_compact(message + b"." + signature, key)

    def test_valid_signatures_work_and_tampering_is_rejected(self):
        key = b"local-regression-key-at-least-32-bytes"
        verifier = JsonWebSignature(algorithms=["HS256"])
        token = verifier.serialize_compact({"alg": "HS256"}, b"fixture", key)
        self.assertEqual(verifier.deserialize_compact(token, key)["payload"], b"fixture")
        protected, _, signature = token.split(b".")
        tampered = b".".join((protected, b"dGFtcGVyZWQ", signature))
        with self.assertRaises(JoseError):
            verifier.deserialize_compact(tampered, key)


if __name__ == "__main__":
    unittest.main()
