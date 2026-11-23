"""Pruebas de humo de la interfaz de consola (sin red ni entrada interactiva)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from signer import main as cli  # falla si main.py no compila
from signer.vault import Vault

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "signer.main", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30)


class CliTest(unittest.TestCase):
    def test_module_exposes_entry_points(self):
        self.assertTrue(callable(cli.main) and callable(cli.cmd_init))
        for method in ("run", "_show", "_decide"):
            self.assertTrue(callable(getattr(cli.ConsoleApprover, method, None)), method)

    def test_run_without_vault_explains_how_to_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--vault", str(Path(tmp) / "no.json"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signer.main init", result.stderr + result.stdout)

    def test_init_never_overwrites_existing_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vault.json"
            Vault(path, scrypt_n=2**14).create_vault("contrasena-larga")
            before = path.read_bytes()
            result = run_cli("init", "--vault", str(path))
            self.assertEqual(result.returncode, 1)
            self.assertIn("No se sobrescribe", result.stdout)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
