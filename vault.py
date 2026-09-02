"""
vault.py — Cofre de Segurança do Orquestra
Criptografia autenticada via Fernet (AES-128-CBC + HMAC-SHA256), com chave
derivada de 256 bits (PBKDF2-SHA256), por projeto.
Cada pasta tem seu próprio .orquestra_vault isolado.
"""

import os
import sys
import json
import base64
import hashlib
import getpass
from pathlib import Path
from typing import Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

VAULT_FILENAME = ".orquestra_vault"
SALT_FILENAME = ".orquestra_salt"
GITIGNORE_ENTRIES = [".orquestra_vault", ".orquestra_salt"]

# ─────────────────────────────────────────────────────────
# Utilitários internos
# ─────────────────────────────────────────────────────────

def _derive_key(password: str, salt: bytes) -> bytes:
    """Deriva uma chave AES-256 da senha mestra usando PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def _get_vault_path(project_dir: str) -> Path:
    return Path(project_dir) / VAULT_FILENAME


def _get_salt_path(project_dir: str) -> Path:
    return Path(project_dir) / SALT_FILENAME


def _update_gitignore(project_dir: str):
    """Adiciona as entradas necessárias ao .gitignore do projeto se não existirem."""
    gitignore = Path(project_dir) / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    
    entries = [".orquestra_vault", ".orquestra_salt", ".orquestra_logs.md"]
    to_add = [e for e in entries if e not in existing]
    
    if to_add:
        with open(gitignore, "a", encoding="utf-8") as f:
            f.write("\n# Orquestra\n")
            for entry in to_add:
                f.write(f"{entry}\n")


def _load_vault_data(fernet: "Fernet", vault_path: Path) -> dict:
    """Lê e decripta o conteúdo do cofre. Retorna {} se vazio."""
    if not vault_path.exists() or vault_path.stat().st_size == 0:
        return {}
    raw = vault_path.read_bytes()
    try:
        decrypted = fernet.decrypt(raw)
        return json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, Exception):
        return None  # Senha errada


def _save_vault_data(fernet: "Fernet", vault_path: Path, data: dict):
    """Encripta e grava o cofre no disco."""
    encrypted = fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    vault_path.write_bytes(encrypted)


# ─────────────────────────────────────────────────────────
# Classe pública — VaultManager
# ─────────────────────────────────────────────────────────

class VaultManager:
    """
    Gerencia o cofre de senhas do projeto.
    Uso típico:
        vault = VaultManager(project_dir)
        if vault.needs_setup():
            vault.setup_wizard()
        else:
            vault.unlock_interactive()
    """

    def __init__(self, project_dir: str):
        if not CRYPTO_AVAILABLE:
            raise RuntimeError(
                "Biblioteca 'cryptography' não instalada. "
                "Rode: pip install cryptography --break-system-packages"
            )
        self.project_dir = project_dir
        self.vault_path = _get_vault_path(project_dir)
        self.salt_path = _get_salt_path(project_dir)
        self._fernet: Optional["Fernet"] = None
        self._data: Optional[dict] = None

    @property
    def is_unlocked(self) -> bool:
        return self._fernet is not None and self._data is not None

    def needs_setup(self) -> bool:
        """True se o cofre ainda não foi criado neste projeto."""
        return not self.vault_path.exists()

    # ── Setup (primeira vez) ──────────────────────────────

    def setup_wizard(self) -> bool:
        """
        Guia interativo de criação do cofre.
        Retorna True se criado com sucesso.
        """
        print()
        print("🔐 \033[1mCofre de Segurança — Configuração Inicial\033[0m")
        print("─" * 52)
        print("Este cofre guarda suas senhas e chaves de API de forma")
        print("segura, criptografadas aqui na sua máquina.")
        print("Nenhum dado sai do seu computador.")
        print()
        print("👉 Escolha uma \033[1msenha mestra\033[0m para proteger este projeto.")
        print("   Você precisará dela toda vez que abrir o Orquestra aqui.")
        print()

        while True:
            pw = getpass.getpass("   Senha mestra (oculta): ")
            if len(pw) < 6:
                print("   ⚠  Use pelo menos 6 caracteres.\n")
                continue
            pw2 = getpass.getpass("   Confirme a senha:       ")
            if pw != pw2:
                print("   ⚠  As senhas não conferem. Tente de novo.\n")
                continue
            break

        # Gera salt aleatório e cria o cofre vazio
        salt = os.urandom(16)
        self.salt_path.write_bytes(salt)
        key = _derive_key(pw, salt)
        self._fernet = Fernet(key)
        self._data = {}
        _save_vault_data(self._fernet, self.vault_path, self._data)
        _update_gitignore(self.project_dir)

        print()
        print("   ✓ \033[32mCofre criado com sucesso!\033[0m")
        print()
        print("   💡 Para salvar uma credencial, use:")
        print("      /senha NOME_DA_CHAVE valor_secreto")
        print("      Exemplo: /senha DEEPSEEK_KEY sk-abc123...")
        print()
        return True

    # ── Unlock (sessões seguintes) ────────────────────────

    def unlock_interactive(self) -> bool:
        """
        Pede a senha mestra e desbloqueia o cofre.
        Retorna True se desbloqueado com sucesso.
        """
        if not self.salt_path.exists():
            print("⚠  Arquivo de salt não encontrado. O cofre pode estar corrompido.")
            return False

        salt = self.salt_path.read_bytes()
        print()
        print("🔐 Cofre detectado. Digite sua senha mestra para desbloquear:")
        print("   (ou pressione Enter para pular — algumas funções ficarão indisponíveis)")
        print()

        for attempt in range(3):
            pw = getpass.getpass("   Senha mestra: ")
            if pw == "":
                print("   ⚠  Cofre não desbloqueado. Credenciais indisponíveis nesta sessão.\n")
                return False

            key = _derive_key(pw, salt)
            fernet = Fernet(key)
            data = _load_vault_data(fernet, self.vault_path)

            if data is None:
                remaining = 2 - attempt
                if remaining > 0:
                    print(f"   ✗ Senha incorreta. {remaining} tentativa(s) restante(s).\n")
                else:
                    print("   ✗ Senha incorreta. Cofre bloqueado por esta sessão.\n")
                continue

            self._fernet = fernet
            self._data = data
            count = len(data)
            label = "credencial" if count == 1 else "credenciais"
            names = ", ".join(data.keys()) if data else "nenhuma ainda"
            print(f"   ✓ \033[32mCofre desbloqueado.\033[0m {count} {label} disponível(is).")
            print(f"   ({names})")
            print()
            return True

        return False

    def unlock_with_password(self, password: str) -> bool:
        """Desbloqueia programaticamente (para testes). Retorna True se OK."""
        if not self.salt_path.exists():
            return False
        salt = self.salt_path.read_bytes()
        key = _derive_key(password, salt)
        fernet = Fernet(key)
        data = _load_vault_data(fernet, self.vault_path)
        if data is None:
            return False
        self._fernet = fernet
        self._data = data
        return True

    # ── Comandos do cofre ─────────────────────────────────

    def save(self, name: str, value: str) -> str:
        """Salva ou atualiza uma credencial. Retorna mensagem de status."""
        if not self.is_unlocked:
            return "✗ Cofre bloqueado. Reinicie o Orquestra para desbloquear."
        name = name.upper().strip()
        if not name:
            return "✗ Nome inválido."
        self._data[name] = value
        _save_vault_data(self._fernet, self.vault_path, self._data)
        return f"✓ \033[32m{name}\033[0m salvo com segurança no cofre."

    def get(self, name: str) -> Optional[str]:
        """Recupera o valor de uma credencial (ou None se não existir)."""
        if not self.is_unlocked:
            return None
        return self._data.get(name.upper().strip())

    def remove(self, name: str) -> str:
        """Remove uma credencial do cofre."""
        if not self.is_unlocked:
            return "✗ Cofre bloqueado."
        name = name.upper().strip()
        if name not in self._data:
            return f"✗ Credencial '{name}' não encontrada."
        del self._data[name]
        _save_vault_data(self._fernet, self.vault_path, self._data)
        return f"✓ \033[32m{name}\033[0m removida do cofre."

    def list_names(self) -> list:
        """Lista os nomes das credenciais salvas (sem os valores)."""
        if not self.is_unlocked:
            return []
        return sorted(self._data.keys())

    def inject_env(self):
        """Injeta as credenciais salvas como variáveis de ambiente desta sessão."""
        if not self.is_unlocked:
            return
        for name, value in self._data.items():
            os.environ[name] = value

    def get_env_vars(self) -> dict:
        """Retorna um dict {NOME: valor} para injetar como env vars."""
        if not self.is_unlocked:
            return {}
        return dict(self._data)

    # ── Processador de comandos /senha ────────────────────

    def handle_command(self, raw_input: str) -> Optional[str]:
        """
        Processa comandos do cofre digitados pelo usuário.
        Retorna a mensagem de resposta (str) ou None se não era um comando do cofre.
        """
        stripped = raw_input.strip()

        # /senha-mestra SENHA — troca ou define a senha mestra
        if stripped.lower().startswith("/senha-mestra"):
            parts = stripped.split(None, 1)
            if len(parts) < 2:
                return "Uso: /senha-mestra [nova_senha]"
            new_pw = parts[1].strip()
            if not self.is_unlocked:
                return "✗ Cofre bloqueado. Não é possível trocar a senha."
            salt = os.urandom(16)
            self.salt_path.write_bytes(salt)
            key = _derive_key(new_pw, salt)
            self._fernet = Fernet(key)
            _save_vault_data(self._fernet, self.vault_path, self._data)
            return "✓ Senha mestra atualizada com sucesso."

        # /senhas — lista todas as credenciais
        if stripped.lower() in ("/senhas", "/cofre", "/cofre-status"):
            names = self.list_names()
            if not names:
                return "🔐 Cofre vazio. Use /senha NOME valor para salvar credenciais."
            return "🔐 Credenciais no cofre:\n" + "\n".join(f"  • {n}" for n in names)

        # /senha-remove NOME
        if stripped.lower().startswith("/senha-remove"):
            parts = stripped.split(None, 1)
            if len(parts) < 2:
                return "Uso: /senha-remove NOME_DA_CREDENCIAL"
            return self.remove(parts[1].strip())

        # /senha NOME valor — salva credencial
        # /senha NOME       — injeta no env e confirma (sem mostrar valor)
        if stripped.lower().startswith("/senha"):
            parts = stripped.split(None, 2)
            if len(parts) == 1:
                return (
                    "Uso:\n"
                    "  /senha NOME valor     → salva uma credencial\n"
                    "  /senha NOME           → injeta no ambiente atual\n"
                    "  /senhas               → lista credenciais salvas\n"
                    "  /senha-remove NOME    → remove uma credencial\n"
                    "  /senha-mestra SENHA   → troca a senha do cofre"
                )
            if len(parts) == 2:
                # /senha NOME — injeta no env sem mostrar o valor
                name = parts[1].upper().strip()
                value = self.get(name)
                if value is None:
                    return f"✗ Credencial '{name}' não encontrada no cofre."
                os.environ[name] = value
                return f"✓ {name} injetada no ambiente desta sessão (valor oculto)."
            if len(parts) == 3:
                # /senha NOME valor — salva
                name = parts[1].strip()
                value = parts[2].strip()
                return self.save(name, value)

        return None  # Não era um comando do cofre


# ─────────────────────────────────────────────────────────
# Testes rápidos (rode: python3 vault.py)
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile, shutil

    print("=== Teste do VaultManager ===\n")
    tmpdir = tempfile.mkdtemp()
    try:
        vault = VaultManager(tmpdir)

        # 1. Verifica que precisa de setup
        assert vault.needs_setup(), "Erro: não deveria ter vault ainda"
        print("✓ needs_setup() = True  (correto)")

        # 2. Cria o cofre com senha de teste
        salt = os.urandom(16)
        (Path(tmpdir) / SALT_FILENAME).write_bytes(salt)
        key = _derive_key("senhateste123", salt)
        vault._fernet = Fernet(key)
        vault._data = {}
        _save_vault_data(vault._fernet, vault.vault_path, vault._data)
        _update_gitignore(tmpdir)
        print("✓ Cofre criado e .gitignore atualizado")

        # 3. Salva uma credencial
        resp = vault.save("MINHA_API_KEY", "sk-abc123xyz")
        assert "MINHA_API_KEY" in resp, f"Falha: {resp}"
        print(f"✓ save(): {resp}")

        # 4. Lista
        names = vault.list_names()
        assert "MINHA_API_KEY" in names, "Erro: chave não listada"
        print(f"✓ list_names(): {names}")

        # 5. Lê de volta
        val = vault.get("MINHA_API_KEY")
        assert val == "sk-abc123xyz", f"Erro: valor incorreto: {val}"
        print(f"✓ get(): valor correto (oculto no log)")

        # 6. Persiste — cria nova instância e desbloqueia
        vault2 = VaultManager(tmpdir)
        ok = vault2.unlock_with_password("senhateste123")
        assert ok, "Erro: não conseguiu desbloquear"
        assert vault2.get("MINHA_API_KEY") == "sk-abc123xyz"
        print("✓ Persistência OK — nova instância leu o mesmo valor")

        # 7. Senha errada
        vault3 = VaultManager(tmpdir)
        ok = vault3.unlock_with_password("senha_errada")
        assert not ok, "Erro: desbloqueou com senha errada!"
        print("✓ Senha errada rejeitada corretamente")

        # 8. Remove
        resp = vault.remove("MINHA_API_KEY")
        assert "MINHA_API_KEY" in resp
        assert vault.get("MINHA_API_KEY") is None
        print(f"✓ remove(): {resp}")

        # 9. handle_command
        vault.save("TOKEN_GH", "ghp_xyz")
        resp = vault.handle_command("/senhas")
        assert "TOKEN_GH" in resp
        print(f"✓ handle_command('/senhas'): OK")

        resp = vault.handle_command("/senha TOKEN_GH valor_novo_aqui")
        assert "TOKEN_GH" in resp
        print(f"✓ handle_command('/senha NOME valor'): OK")

        resp = vault.handle_command("mensagem normal")
        assert resp is None
        print("✓ handle_command('mensagem normal'): retorna None (correto)")

        print("\n\033[32m✓ Todos os testes passaram!\033[0m")

    finally:
        shutil.rmtree(tmpdir)
