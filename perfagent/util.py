import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_env.filters["tojs"] = lambda v: json.dumps(v, ensure_ascii=False)


def render(template: str, **ctx) -> str:
    return _env.get_template(template).render(**ctx)


def read_yaml(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=110)


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def safe_tag(text: str) -> str:
    """k6 threshold submetric filters break on ':', '{', '}' and ','."""
    return re.sub(r"[^A-Za-z0-9 _\-./]", "", str(text)).strip()


ID_SEGMENT = re.compile(r"^(\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{24,})$")


def normalize_path(path: str) -> str:
    """Replace id-like path segments with {id} so dynamic URLs group together."""
    return "/".join("{id}" if ID_SEGMENT.match(seg) else seg for seg in path.split("/"))


def read_csv_rows(path: Path) -> list[dict]:
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(line for line in f if line.strip() and not line.lstrip().startswith("#"))]
