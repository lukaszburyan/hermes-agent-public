#!/usr/bin/env python3
"""Fail-fast checks for the isolated Orchesta final-offer runtime."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any

import tenant_config

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "rfq-final-offer"


def smoke_test_pdf(work_dir: Path) -> dict[str, Any]:
    """Render and reopen one synthetic offer with the production skill."""
    script = SKILL / "scripts" / "rfq_final_offer.py"
    try:
        spec = importlib.util.spec_from_file_location("rfq_preflight_offer", script)
        if spec is None or spec.loader is None:
            raise RuntimeError("skill_import_unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="rfq-preflight-pdf-", dir=work_dir) as tmp:
            output = Path(tmp)
            data = module.sample_data(2, True)
            data["offer_number"] = "ORCH-RFQ-PREFLIGHT-0001"
            data["client"].update(
                {
                    "first_name": "Anna",
                    "last_name": "Testowa",
                    "company": "HERMES Preflight Test",
                    "email": "preflight@example.invalid",
                }
            )
            pricing = module.load_json(module.DEFAULT_PRICING_PATH)
            context = module.build_offer_context(data, pricing)
            html_path = output / "preflight.html"
            pdf_path = output / "preflight.pdf"
            html_path.write_text(module.render_offer_html(context), encoding="utf-8")
            module.render_pdf(html_path, pdf_path)
            validation = module.validate_pdf(pdf_path, context)
            if not validation.get("ok"):
                raise RuntimeError("pdf_validation_failed")
            return {
                "ok": True,
                "size_bytes": pdf_path.stat().st_size,
                "page_count": validation.get("page_count"),
                "header": pdf_path.read_bytes()[:5].decode("ascii", errors="replace"),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{exc.__class__.__name__}:{str(exc)[:120]}",
        }


def check_runtime(tenant_id: str, work_dir: Path) -> dict[str, object]:
    errors: list[str] = []
    for module in ("jinja2", "weasyprint", "pypdf"):
        if importlib.util.find_spec(module) is None:
            errors.append(f"missing_dependency:{module}")
    for relative in (
        "SKILL.md",
        "scripts/rfq_final_offer.py",
        "scripts/requirements.txt",
        "templates/orchesta_offer.html.j2",
        "templates/orchesta_offer.css",
        "templates/mail_final_offer_pl.txt.j2",
        "rfq-final-offer-knowledge/approved/pricing.json",
    ):
        if not (SKILL / relative).is_file():
            errors.append(f"missing_skill_file:{relative}")
    errors.extend(f"missing_tenant_file:{name}" for name in tenant_config.validate_tenant_package(tenant_id))
    work_dir_ready = False
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="rfq-preflight-", dir=work_dir):
            pass
        work_dir_ready = True
    except OSError as exc:
        errors.append(f"work_dir_not_writable:{exc.__class__.__name__}")
    pdf_smoke: dict[str, Any] = {"ok": False, "error": "not_run"}
    if not errors and work_dir_ready:
        pdf_smoke = smoke_test_pdf(work_dir)
        if not pdf_smoke.get("ok"):
            errors.append(f"pdf_smoke_failed:{pdf_smoke.get('error', 'unknown')}")
    return {
        "ok": not errors,
        "tenant_id": tenant_id,
        "work_dir": str(work_dir),
        "pdf_smoke": pdf_smoke,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="orchesta")
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    result = check_runtime(args.tenant, args.work_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
