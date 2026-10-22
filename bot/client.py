"""Активний клієнт: чиї дані рушій обробляє в цьому процесі.

Один процес = один клієнт. Клієнт обирається змінною `JOBSEARCH_CLIENT`
(або прапорцем `--client <slug>`, який `bootstrap_argv()` перекладає в ту
саму змінну ще до імпорту решти модулів). Дані клієнта лежать поза цим
репозиторієм, у `JOBSEARCH_CLIENTS_DIR/<slug>/`:

    profile.yaml     контакти й поля форм
    cv.yaml          досвід, освіта, скіли, вкладення
    tracks.yaml      треки: ключові слова, варіант CV, пріоритет, блоки листа
    targeting.yaml   локації, виключені назви ролей, пороги
    search.yaml      пошукові запити й кар'єрні сторінки (для агент-промптів)
    accounts.yaml    поштові акаунти, браузерний профіль
    candidate_brief.md  вільний текст для tailoring
    attachments/  applications/  var/  tracker.md

Модулі `bot.apply.candidate`, `bot.generators.keywords`, `bot.filters.rules`
тощо читають звідси свої константи під час імпорту, тому клієнт має бути
обраний раніше.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
ENV_CLIENT = "JOBSEARCH_CLIENT"
ENV_CLIENTS_DIR = "JOBSEARCH_CLIENTS_DIR"
STATUSES = ("active", "paused", "placed")


class ClientError(RuntimeError):
    pass


def clients_dir() -> Path:
    """Каталог з папками клієнтів: env або сусідній репо jobsearch-clients."""
    env = os.environ.get(ENV_CLIENTS_DIR)
    if env:
        return Path(env).expanduser()
    return ENGINE_ROOT.parent / "jobsearch-clients" / "clients"


def bootstrap_argv(argv: list[str] | None = None) -> None:
    """Виймає `--client <slug>` / `--client=<slug>` з argv у змінну оточення.

    Викликати першим рядком CLI-скрипта, до імпорту модулів `bot.*`.
    """
    argv = sys.argv if argv is None else argv
    for i, arg in enumerate(argv):
        if arg == "--client" and i + 1 < len(argv):
            os.environ[ENV_CLIENT] = argv[i + 1]
            del argv[i:i + 2]
            return
        if arg.startswith("--client="):
            os.environ[ENV_CLIENT] = arg.split("=", 1)[1]
            del argv[i]
            return


def _load_yaml(path: Path, required: bool = True) -> dict:
    import yaml

    if not path.exists():
        if required:
            raise ClientError(f"немає файла конфігу клієнта: {path}")
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@dataclass(frozen=True)
class Client:
    slug: str
    root: Path
    profile: dict
    cv: dict
    tracks: dict
    targeting: dict
    accounts: dict = field(default_factory=dict)
    search: dict = field(default_factory=dict)

    # ── шляхи ────────────────────────────────────────────────────────────
    @property
    def tracker(self) -> Path:
        return self.root / "tracker.md"

    @property
    def applications(self) -> Path:
        return self.root / "applications"

    @property
    def attachments(self) -> Path:
        return self.root / "attachments"

    @property
    def var(self) -> Path:
        return self.root / "var"

    @property
    def run_dir(self) -> Path:
        return self.var / "run"

    @property
    def photo(self) -> Path | None:
        name = self.profile.get("photo")
        return self.root / name if name else None

    @property
    def brief(self) -> str:
        path = self.root / "candidate_brief.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    # ── похідні значення ─────────────────────────────────────────────────
    @property
    def status(self) -> str:
        return self.profile.get("status", "active")

    @property
    def full_name(self) -> str:
        return f"{self.profile['first_name']} {self.profile['last_name']}"

    @property
    def file_stem(self) -> str:
        """Префікс імен файлів CV: Vorname_Nachname."""
        return self.full_name.replace(" ", "_")

    def gmail(self, alias: str) -> dict:
        try:
            return self.accounts["gmail"][alias]
        except KeyError:
            raise ClientError(
                f"клієнт {self.slug}: у accounts.yaml немає gmail.{alias}") from None

    @property
    def send_address(self) -> str:
        """Адреса, з якої йдуть нові заявки."""
        return self.gmail(self.send_from)["address"]

    @property
    def report_email(self) -> str:
        """Особиста адреса кандидатки для короткого звіту про подані заявки.

        Порожньо = звіт кандидатці не шлеться, усе йде лише оператору.
        """
        return str(self.profile.get("report_email") or "").strip()

    @property
    def send_from(self) -> str:
        return self.accounts.get("send_from", "jobs")

    @property
    def gmail_aliases(self) -> list[str]:
        """Алiаси скриньок з accounts.yaml у порядку оголошення.

        `main` не обов'язковий: у клієнта може не бути особистої скриньки на
        Gmail, тоді відповіді роботодавців падають на `jobs`.
        """
        return list(self.accounts.get("gmail") or {})

    @property
    def reply_account(self) -> str:
        """Скринька, куди роботодавці відповідають: `main`, якщо є, інакше та,
        з якої шлемо (її адреса стоїть у CV)."""
        return "main" if "main" in self.gmail_aliases else self.send_from

    # ── OAuth-файли Gmail ─────────────────────────────────────────────────
    # Ключ OAuth-клієнта Google Cloud може бути свій у клієнта
    # (~/.gmail-mcp/<creds_dir>/gcp-oauth.keys.json: проєкт під її власним
    # Google-акаунтом, повна ізоляція) або один на машину
    # (~/.gmail-mcp/gcp-oauth.keys.json, як було спочатку).
    def gmail_dir(self, home: Path | None = None) -> Path:
        return (home or Path.home()) / ".gmail-mcp"

    def credentials_path(self, alias: str, home: Path | None = None) -> Path:
        return self.gmail_dir(home) / str(self.gmail(alias)["creds_dir"]) / "credentials.json"

    def oauth_keys_path(self, alias: str, home: Path | None = None) -> Path:
        own = self.gmail_dir(home) / str(self.gmail(alias)["creds_dir"]) / "gcp-oauth.keys.json"
        return own if own.exists() else self.gmail_dir(home) / "gcp-oauth.keys.json"

    def account_for_address(self, address: str) -> str:
        """Алiас скриньки за адресою (напр. candidate.EMAIL), інакше `reply_account`."""
        wanted = (address or "").strip().lower()
        for alias, acc in (self.accounts.get("gmail") or {}).items():
            if str(acc.get("address", "")).strip().lower() == wanted:
                return alias
        return self.reply_account

    @property
    def playwright_profile(self) -> str:
        return self.accounts.get("playwright_profile", self.slug)


def load(slug: str, base: Path | None = None) -> Client:
    root = (base or clients_dir()) / slug
    if not root.is_dir():
        raise ClientError(f"клієнта '{slug}' не знайдено: {root}")
    profile = _load_yaml(root / "profile.yaml")
    status = profile.get("status", "active")
    if status not in STATUSES:
        raise ClientError(f"клієнт {slug}: status '{status}' не з {STATUSES}")
    return Client(
        slug=slug,
        root=root,
        profile=profile,
        cv=_load_yaml(root / "cv.yaml"),
        tracks=_load_yaml(root / "tracks.yaml"),
        targeting=_load_yaml(root / "targeting.yaml"),
        accounts=_load_yaml(root / "accounts.yaml", required=False),
        search=_load_yaml(root / "search.yaml", required=False),
    )


@lru_cache(maxsize=1)
def active() -> Client:
    slug = os.environ.get(ENV_CLIENT)
    if not slug:
        raise ClientError(
            f"клієнта не обрано: задай {ENV_CLIENT}=<slug> або --client <slug> "
            f"(каталог клієнтів: {clients_dir()})")
    return load(slug)


def list_clients(base: Path | None = None) -> list[Client]:
    base = base or clients_dir()
    if not base.is_dir():
        return []
    found = []
    for path in sorted(base.iterdir()):
        if path.is_dir() and (path / "profile.yaml").exists():
            found.append(load(path.name, base))
    return found
