import os
from pathlib import Path
from urllib.parse import urlparse

from perfagent.util import read_yaml

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"
PROJECT_TEMPLATE = ROOT / "perfagent" / "project_template.yaml"
K6_LIB = ROOT / "perfagent" / "k6lib"

DEFAULT_SLA = {"p95_ms": 1000, "p99_ms": 2000, "error_rate": 0.01, "lcp_ms": 2500}


class Project:
    def __init__(self, name: str):
        self.name = name
        self.dir = PROJECTS / name
        cfg_file = self.dir / "project.yaml"
        if not cfg_file.exists():
            raise SystemExit(f"Project '{name}' not found. Create it with: python -m perfagent init {name}")
        self.cfg = read_yaml(cfg_file)

    def load_env(self) -> None:
        """Load git-ignored projects/<name>/.env (KEY=VALUE lines) for secrets; real env vars take precedence."""
        env_file = self.dir / ".env"
        if not env_file.exists():
            return
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    # ---- paths -------------------------------------------------------
    @property
    def model_dir(self) -> Path:
        return self.dir / "model"

    @property
    def catalog_file(self) -> Path:
        return self.dir / "catalog.yaml"

    @property
    def design_dir(self) -> Path:
        return self.dir / "design"

    @property
    def scripts_dir(self) -> Path:
        return self.dir / "scripts"

    @property
    def data_dir(self) -> Path:
        return self.dir / "data"

    @property
    def runs_dir(self) -> Path:
        return self.dir / "runs"

    # ---- config accessors -------------------------------------------
    @property
    def target(self) -> dict:
        return self.cfg.get("target") or {}

    @property
    def nfr(self) -> dict:
        return self.cfg.get("nfr") or {}

    @property
    def default_sla(self) -> dict:
        return {**DEFAULT_SLA, **(self.nfr.get("default_sla") or {})}

    @property
    def environment(self) -> str:
        return str(self.cfg.get("environment", "test")).lower()

    def allowed_hosts(self) -> list[str]:
        return [h.lower() for h in (self.cfg.get("safety") or {}).get("allowed_hosts") or []]

    def check_host_allowed(self, url: str) -> None:
        if not url:
            return
        host = (urlparse(url).hostname or "").lower()
        allowed = self.allowed_hosts()
        if not allowed:
            raise SystemExit("Safety: safety.allowed_hosts is empty in project.yaml - list the hosts you are authorised to load test.")
        if not any(host == h or host.endswith("." + h) for h in allowed):
            raise SystemExit(f"Safety: host '{host}' is not in safety.allowed_hosts {allowed}. Refusing to send load.")
