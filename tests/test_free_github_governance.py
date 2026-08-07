from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_every_change_is_routed_to_the_repository_owner():
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "* @lukaszburyan" in codeowners


def test_pull_request_template_requires_release_evidence_and_rollback():
    template = (ROOT / ".github" / "pull_request_template.md").read_text(
        encoding="utf-8"
    )
    for heading in ("## Zmiany", "## Ryzyko", "## Testy i dowody", "## Rollback"):
        assert heading in template


def test_main_push_is_audited_for_merged_pull_request_provenance():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "Main commit came from a merged PR" in workflow
    assert "/commits/${GITHUB_SHA}/pulls" in workflow
    assert "main-provenance.json" in workflow
    assert "pull-requests: read" in workflow


def test_secret_scan_is_deterministic_for_all_refs_and_unrelated_histories():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "fetch-depth: 0" in workflow
    assert "--log-opts=--all" in workflow
    assert "9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c" in workflow
    assert "Upload Gitleaks evidence" in workflow
    assert "gitleaks/gitleaks-action@" not in workflow
