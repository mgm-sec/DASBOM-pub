"""
Tests for scripts/python/09_security_audit.py

Covers:
  - cvss3_score  — CVSS v3 vector string → float
  - parse_purl   — PURL string → (ecosystem, namepath)
  - purl_to_osv  — PURL → (ecosystem_str, name_str) tuple or None
  - severity_of  — vuln object → severity string (from database_specific.severity)
  - max_severity — list of vulns → worst severity
  - max_cvss     — list of vulns → highest float score
  - fixed_in     — vuln → first fix version or None
  - compact_vuln — vuln → trimmed dict
"""

import pytest
from conftest import load_script

m = load_script("09_security_audit.py")


# ─────────────────────────────────────────────────────────────────────────────
# cvss3_score
# ─────────────────────────────────────────────────────────────────────────────
class TestCvss3Score:
    def test_known_high_vector(self):
        # AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H → critical, ≥ 9.0
        v = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
        score = m.cvss3_score(v)
        assert 9.0 <= score <= 10.0

    def test_low_vector(self):
        # AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N → low
        v = "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"
        score = m.cvss3_score(v)
        assert 0.0 <= score < 4.0

    def test_none_returns_none(self):
        assert m.cvss3_score(None) is None

    def test_empty_returns_none(self):
        assert m.cvss3_score("") is None

    def test_garbage_returns_none(self):
        assert m.cvss3_score("not-a-vector") is None

    def test_score_is_float(self):
        v = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
        assert isinstance(m.cvss3_score(v), float)

    def test_score_one_decimal(self):
        v = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
        score = m.cvss3_score(v)
        assert round(score, 1) == score


# ─────────────────────────────────────────────────────────────────────────────
# parse_purl — returns (ecosystem, namepath), version is NOT returned
# ─────────────────────────────────────────────────────────────────────────────
class TestParsePurl:
    def test_npm(self):
        eco, name = m.parse_purl("pkg:npm/lodash@4.17.21")
        assert eco == "npm"
        assert name == "lodash"

    def test_pypi(self):
        eco, name = m.parse_purl("pkg:pypi/requests@2.28.0")
        assert eco == "pypi"
        assert name == "requests"

    def test_no_version_still_extracts_name(self):
        eco, name = m.parse_purl("pkg:npm/lodash")
        assert eco == "npm"
        assert name == "lodash"

    def test_scoped_npm(self):
        eco, name = m.parse_purl("pkg:npm/%40babel/core@7.0.0")
        assert eco == "npm"
        assert "babel" in name.lower() or "@" in name

    def test_invalid_returns_none_tuple(self):
        eco, name = m.parse_purl("not-a-purl")
        assert eco is None
        assert name is None

    def test_golang(self):
        eco, name = m.parse_purl("pkg:golang/github.com/gin-gonic/gin@v1.9.0")
        assert eco == "golang"
        assert "gin" in name

    def test_missing_prefix_returns_none(self):
        eco, name = m.parse_purl("")
        assert eco is None


# ─────────────────────────────────────────────────────────────────────────────
# purl_to_osv — returns (ecosystem_str, name_str) tuple, or None
# ─────────────────────────────────────────────────────────────────────────────
class TestPurlToOsv:
    def test_npm_returns_tuple(self):
        result = m.purl_to_osv("pkg:npm/lodash@4.17.21")
        assert result is not None
        assert isinstance(result, tuple)
        assert result[0] == "npm"
        assert result[1] == "lodash"

    def test_pypi_ecosystem(self):
        result = m.purl_to_osv("pkg:pypi/requests@2.28.0")
        assert result is not None
        assert result[0] == "PyPI"
        assert result[1] == "requests"

    def test_golang(self):
        result = m.purl_to_osv("pkg:golang/github.com/gin-gonic/gin@v1.9.0")
        assert result is not None
        assert result[0] == "Go"

    def test_cargo(self):
        result = m.purl_to_osv("pkg:cargo/serde@1.0")
        assert result is not None
        assert result[0] == "crates.io"

    def test_invalid_returns_none(self):
        assert m.purl_to_osv("not-a-purl") is None

    def test_unknown_ecosystem_returns_none(self):
        result = m.purl_to_osv("pkg:unknown/foo@1.0")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# severity_of — reads from database_specific.severity, returns "UNKNOWN" if absent
# ─────────────────────────────────────────────────────────────────────────────
def make_vuln_with_db_severity(severity: str):
    return {"id": "GHSA-test", "database_specific": {"severity": severity}, "affected": []}


class TestSeverityOf:
    def test_critical(self):
        v = make_vuln_with_db_severity("CRITICAL")
        assert m.severity_of(v) == "CRITICAL"

    def test_high(self):
        v = make_vuln_with_db_severity("HIGH")
        assert m.severity_of(v) == "HIGH"

    def test_medium(self):
        v = make_vuln_with_db_severity("MEDIUM")
        assert m.severity_of(v) == "MEDIUM"

    def test_low(self):
        v = make_vuln_with_db_severity("LOW")
        assert m.severity_of(v) == "LOW"

    def test_no_database_specific_returns_unknown(self):
        v = {"id": "GHSA-x", "affected": []}
        assert m.severity_of(v) == "UNKNOWN"

    def test_empty_severity_returns_unknown(self):
        v = {"id": "GHSA-x", "database_specific": {"severity": ""}, "affected": []}
        assert m.severity_of(v) == "UNKNOWN"

    def test_case_insensitive_input(self):
        v = make_vuln_with_db_severity("critical")
        assert m.severity_of(v) == "CRITICAL"


# ─────────────────────────────────────────────────────────────────────────────
# max_severity / max_cvss
# ─────────────────────────────────────────────────────────────────────────────
class TestMaxSeverity:
    def test_empty_list_returns_none(self):
        assert m.max_severity([]) is None

    def test_critical_wins(self):
        vulns = [
            make_vuln_with_db_severity("HIGH"),
            make_vuln_with_db_severity("CRITICAL"),
            make_vuln_with_db_severity("LOW"),
        ]
        assert m.max_severity(vulns) == "CRITICAL"

    def test_high_beats_medium(self):
        vulns = [make_vuln_with_db_severity("MEDIUM"), make_vuln_with_db_severity("HIGH")]
        assert m.max_severity(vulns) == "HIGH"

    def test_single_vuln(self):
        assert m.max_severity([make_vuln_with_db_severity("LOW")]) == "LOW"


class TestMaxCvss:
    def test_returns_highest(self):
        vulns = [
            {"id": "a", "severity": [{"score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]},
            {"id": "b", "severity": [{"score": "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"}]},
        ]
        result = m.max_cvss(vulns)
        assert result is not None
        assert result >= 9.0

    def test_empty(self):
        assert m.max_cvss([]) is None

    def test_no_severity_field(self):
        vulns = [{"id": "x", "affected": []}]
        assert m.max_cvss(vulns) is None


# ─────────────────────────────────────────────────────────────────────────────
# fixed_in
# ─────────────────────────────────────────────────────────────────────────────
class TestFixedIn:
    def test_extracts_fix_version(self):
        vuln = {
            "id": "GHSA-x",
            "affected": [{
                "package": {"name": "lodash", "ecosystem": "npm"},
                "ranges": [{"type": "SEMVER", "events": [
                    {"introduced": "0"}, {"fixed": "4.17.22"}
                ]}]
            }]
        }
        assert m.fixed_in(vuln) == "4.17.22"

    def test_no_fixed_event_returns_none(self):
        vuln = {
            "id": "GHSA-x",
            "affected": [{
                "package": {"name": "lodash", "ecosystem": "npm"},
                "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}]}]
            }]
        }
        assert m.fixed_in(vuln) is None

    def test_empty_affected(self):
        assert m.fixed_in({"id": "GHSA-x", "affected": []}) is None

    def test_multiple_ranges_first_fix(self):
        vuln = {
            "id": "GHSA-x",
            "affected": [{
                "ranges": [
                    {"type": "SEMVER", "events": [{"introduced": "1.0"}, {"fixed": "1.0.5"}]},
                    {"type": "SEMVER", "events": [{"introduced": "2.0"}, {"fixed": "2.1.0"}]},
                ]
            }]
        }
        fix = m.fixed_in(vuln)
        assert fix in ("1.0.5", "2.1.0")


# ─────────────────────────────────────────────────────────────────────────────
# compact_vuln
# ─────────────────────────────────────────────────────────────────────────────
class TestCompactVuln:
    def test_has_id(self):
        v = {"id": "CVE-2023-1234", "modified": "2023-01-01", "aliases": []}
        c = m.compact_vuln(v)
        assert c["id"] == "CVE-2023-1234"

    def test_summary_truncated_at_200(self):
        v = {"id": "CVE-2023-1234", "aliases": [], "summary": "x" * 500}
        c = m.compact_vuln(v)
        assert len(c["summary"]) <= 200

    def test_alias_prefers_cve(self):
        v = {
            "id": "GHSA-xxxx-yyyy-zzzz",
            "aliases": ["CVE-2023-9999", "OSVDB-1234"],
        }
        c = m.compact_vuln(v)
        assert c["alias"] == "CVE-2023-9999"

    def test_returns_dict(self):
        assert isinstance(m.compact_vuln({"id": "x", "aliases": []}), dict)

    def test_has_osv_url(self):
        v = {"id": "GHSA-test-0001", "aliases": []}
        c = m.compact_vuln(v)
        assert "osv.dev" in c.get("url", "")

    def test_severity_in_output(self):
        v = {"id": "x", "aliases": [], "database_specific": {"severity": "HIGH"}}
        c = m.compact_vuln(v)
        assert c["severity"] == "HIGH"
