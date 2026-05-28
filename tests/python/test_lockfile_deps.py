"""
Tests for scripts/python/08b_lockfile_deps.py

All parse_* functions return a list of (name, version, deps) tuples where
deps is a list of dep names.

Covers:
  - parse_package_lock_json  — npm lockfile v2 (incl. nested node_modules)
  - parse_poetry_lock        — poetry.lock TOML-ish format
  - parse_yarn_lock_v1       — yarn.lock v1 classic
  - parse_yarn_lock_v2       — yarn.lock v2 berry
  - parse_yarn_lock          — dispatcher (v1/v2 auto-detect)
  - parse_cargo_lock         — Cargo.lock
  - parse_composer_lock      — composer.lock JSON (excl. php/ext-/lib-)
  - parse_go_mod             — go.mod direct deps
  - parse_gemfile_lock       — Gemfile.lock GEM/specs section
  - make_purl                — purl construction
  - make_lockfile_stub       — stub node creation
  - eco_key                  — lookup key normalisation
  - norm_pypi / norm_npm     — name normalisation
"""

import json
import math
import pytest
from pathlib import Path
from conftest import load_script, FIXTURES

m = load_script("08b_lockfile_deps.py")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — parsers all return list of (name, version, deps) tuples
# ─────────────────────────────────────────────────────────────────────────────
def names(items):
    return [i[0] for i in items]

def version_of(items, pkg_name):
    for name, ver, _ in items:
        if pkg_name in name:
            return ver
    return None

def deps_of(items, pkg_name):
    for name, ver, deps in items:
        if pkg_name in name:
            return deps
    return None


# ─────────────────────────────────────────────────────────────────────────────
# norm helpers
# ─────────────────────────────────────────────────────────────────────────────
class TestNormPypi:
    def test_lowercases(self):
        assert m.norm_pypi("Requests") == "requests"

    def test_replaces_underscores(self):
        assert m.norm_pypi("my_package") == "my-package"

    def test_replaces_dots(self):
        assert m.norm_pypi("zope.interface") == "zope-interface"

    def test_already_normalised(self):
        assert m.norm_pypi("requests") == "requests"

    def test_mixed(self):
        assert m.norm_pypi("My_Package.Name") == "my-package-name"


class TestNormNpm:
    def test_lowercases(self):
        assert m.norm_npm("Lodash") == "lodash"

    def test_preserves_scope(self):
        result = m.norm_npm("@babel/core")
        assert "@babel" in result or "babel" in result

    def test_already_normalised(self):
        assert m.norm_npm("express") == "express"


# ─────────────────────────────────────────────────────────────────────────────
# eco_key
# ─────────────────────────────────────────────────────────────────────────────
class TestEcoKey:
    def test_pypi_normalises(self):
        assert m.eco_key("pypi", "My_Pkg") == ("pypi", "my-pkg")

    def test_npm_lowercases(self):
        assert m.eco_key("npm", "Lodash") == ("npm", "lodash")

    def test_cargo_lowercases(self):
        assert m.eco_key("cargo", "Serde") == ("cargo", "serde")

    def test_gem_lowercases(self):
        assert m.eco_key("gem", "Rails") == ("gem", "rails")

    def test_golang_lowercases(self):
        assert m.eco_key("golang", "Github.com/Foo/Bar") == ("golang", "github.com/foo/bar")


# ─────────────────────────────────────────────────────────────────────────────
# make_purl
# ─────────────────────────────────────────────────────────────────────────────
class TestMakePurl:
    def test_basic(self):
        assert m.make_purl("npm", "lodash", "4.17.21") == "pkg:npm/lodash@4.17.21"

    def test_scoped_npm(self):
        purl = m.make_purl("npm", "@babel/core", "7.0.0")
        assert purl.startswith("pkg:npm/")
        assert "7.0.0" in purl

    def test_pypi(self):
        assert m.make_purl("pypi", "requests", "2.28.0") == "pkg:pypi/requests@2.28.0"

    def test_golang_path(self):
        purl = m.make_purl("golang", "github.com/gin-gonic/gin", "1.9.1")
        assert purl.startswith("pkg:golang/")
        assert "1.9.1" in purl


# ─────────────────────────────────────────────────────────────────────────────
# make_lockfile_stub
# ─────────────────────────────────────────────────────────────────────────────
class TestMakeLockfileStub:
    def setup_method(self):
        self.node = m.make_lockfile_stub("npm", "lodash", "4.17.21")

    def test_key_is_purl(self):
        assert self.node["key"] == "pkg:npm/lodash@4.17.21"

    def test_type_package(self):
        assert self.node["attributes"]["type"] == "package"

    def test_in_org_false(self):
        assert self.node["attributes"]["in_org"] is False

    def test_src_lockfile(self):
        assert self.node["attributes"]["_src"] == "lockfile"

    def test_has_position(self):
        a = self.node["attributes"]
        assert isinstance(a["x"], float)
        assert isinstance(a["y"], float)

    def test_position_at_outer_ring(self):
        a = self.node["attributes"]
        r = math.sqrt(a["x"] ** 2 + a["y"] ** 2)
        assert 13000 <= r <= 15001

    def test_name_and_version(self):
        a = self.node["attributes"]
        assert a["name"] == "lodash"
        assert a["version"] == "4.17.21"


# ─────────────────────────────────────────────────────────────────────────────
# parse_package_lock_json
# ─────────────────────────────────────────────────────────────────────────────
class TestParsePackageLockJson:
    def setup_method(self):
        self.items = m.parse_package_lock_json(FIXTURES / "package-lock.json")

    def test_returns_list(self):
        assert isinstance(self.items, list)

    def test_each_item_is_triple(self):
        for item in self.items:
            assert len(item) == 3

    def test_contains_lodash(self):
        assert any("lodash" in n for n in names(self.items))

    def test_contains_express(self):
        assert any("express" in n for n in names(self.items))

    def test_lodash_version(self):
        assert version_of(self.items, "lodash") == "4.17.21"

    def test_express_version(self):
        assert version_of(self.items, "express") == "4.18.2"

    def test_root_package_excluded(self):
        assert "" not in names(self.items)

    def test_nested_node_modules_flattened(self, tmp_path):
        """node_modules/a/node_modules/b → name 'b', not 'a/node_modules/b'."""
        data = {
            "packages": {
                "": {"version": "1.0.0"},
                "node_modules/outer": {"version": "1.0.0"},
                "node_modules/outer/node_modules/inner": {"version": "2.0.0"},
            }
        }
        lock = tmp_path / "package-lock.json"
        lock.write_text(json.dumps(data))
        items = m.parse_package_lock_json(lock)
        ns = names(items)
        assert "inner" in ns
        assert "outer/node_modules/inner" not in ns
        assert "outer" in ns


# ─────────────────────────────────────────────────────────────────────────────
# parse_poetry_lock
# ─────────────────────────────────────────────────────────────────────────────
class TestParsePoetryLock:
    def setup_method(self):
        self.items = m.parse_poetry_lock(FIXTURES / "poetry.lock")

    def test_returns_list(self):
        assert isinstance(self.items, list)

    def test_each_item_is_triple(self):
        for item in self.items:
            assert len(item) == 3

    def test_contains_requests(self):
        assert any("requests" in n for n in names(self.items))

    def test_contains_certifi(self):
        assert any("certifi" in n for n in names(self.items))

    def test_requests_version(self):
        assert version_of(self.items, "requests") == "2.28.0"

    def test_certifi_version(self):
        assert version_of(self.items, "certifi") == "2023.5.7"


# ─────────────────────────────────────────────────────────────────────────────
# parse_yarn_lock_v1
# ─────────────────────────────────────────────────────────────────────────────
class TestParseYarnLockV1:
    def setup_method(self):
        self.items = m.parse_yarn_lock_v1(FIXTURES / "yarn.lock.v1")

    def test_returns_list(self):
        assert isinstance(self.items, list)

    def test_detects_v1_header(self):
        assert len(self.items) > 0

    def test_contains_lodash(self):
        assert any("lodash" in n for n in names(self.items))

    def test_lodash_version(self):
        assert version_of(self.items, "lodash") == "4.17.21"

    def test_babel_core_version(self):
        assert version_of(self.items, "@babel/core") == "7.21.4"

    def test_babel_core_deps(self):
        deps = deps_of(self.items, "@babel/core")
        assert deps is not None
        assert "semver" in deps

    def test_rejects_v2_file(self):
        items = m.parse_yarn_lock_v1(FIXTURES / "yarn.lock.v2")
        assert items == []


# ─────────────────────────────────────────────────────────────────────────────
# parse_yarn_lock_v2
# ─────────────────────────────────────────────────────────────────────────────
class TestParseYarnLockV2:
    def setup_method(self):
        self.items = m.parse_yarn_lock_v2(FIXTURES / "yarn.lock.v2")

    def test_returns_list(self):
        assert isinstance(self.items, list)

    def test_detects_metadata_header(self):
        assert len(self.items) > 0

    def test_contains_lodash(self):
        assert any("lodash" in n for n in names(self.items))

    def test_lodash_version(self):
        assert version_of(self.items, "lodash") == "4.17.21"

    def test_contains_express(self):
        assert any("express" in n for n in names(self.items))

    def test_express_version(self):
        assert version_of(self.items, "express") == "4.18.2"

    def test_express_deps_include_lodash(self):
        deps = deps_of(self.items, "express")
        assert deps is not None
        assert "lodash" in deps

    def test_rejects_v1_file(self):
        items = m.parse_yarn_lock_v2(FIXTURES / "yarn.lock.v1")
        assert items == []


# ─────────────────────────────────────────────────────────────────────────────
# parse_yarn_lock dispatcher
# ─────────────────────────────────────────────────────────────────────────────
class TestParseYarnLockDispatch:
    def test_v1_dispatched_correctly(self):
        items = m.parse_yarn_lock(FIXTURES / "yarn.lock.v1")
        assert any("lodash" in n for n in names(items))
        assert version_of(items, "lodash") == "4.17.21"

    def test_v2_dispatched_correctly(self):
        items = m.parse_yarn_lock(FIXTURES / "yarn.lock.v2")
        assert any("express" in n for n in names(items))
        assert version_of(items, "express") == "4.18.2"


# ─────────────────────────────────────────────────────────────────────────────
# parse_cargo_lock
# ─────────────────────────────────────────────────────────────────────────────
class TestParseCargoLock:
    def setup_method(self):
        self.items = m.parse_cargo_lock(FIXTURES / "Cargo.lock")

    def test_returns_list(self):
        assert isinstance(self.items, list)

    def test_each_item_is_triple(self):
        for item in self.items:
            assert len(item) == 3

    def test_contains_serde(self):
        assert any("serde" in n for n in names(self.items))

    def test_contains_tokio(self):
        assert any("tokio" in n for n in names(self.items))

    def test_serde_version(self):
        assert version_of(self.items, "serde") == "1.0.160"

    def test_tokio_version(self):
        assert version_of(self.items, "tokio") == "1.28.0"

    def test_tokio_depends_on_serde(self):
        deps = deps_of(self.items, "tokio")
        assert deps is not None
        assert any("serde" in d for d in deps)


# ─────────────────────────────────────────────────────────────────────────────
# parse_composer_lock
# ─────────────────────────────────────────────────────────────────────────────
class TestParseComposerLock:
    def setup_method(self):
        self.items = m.parse_composer_lock(FIXTURES / "composer.lock")

    def test_returns_list(self):
        assert isinstance(self.items, list)

    def test_each_item_is_triple(self):
        for item in self.items:
            assert len(item) == 3

    def test_contains_symfony_console(self):
        assert any("symfony" in n or "console" in n for n in names(self.items))

    def test_contains_psr_log(self):
        assert any("psr" in n or "log" in n for n in names(self.items))

    def test_symfony_version(self):
        ver = version_of(self.items, "symfony")
        assert ver in ("6.2.7", "v6.2.7")

    def test_php_constraint_excluded(self, tmp_path):
        data = {"packages": [{"name": "vendor/pkg", "version": "1.0.0",
                               "require": {"php": ">=8.0", "ext-json": "*",
                                           "lib-curl": "*", "vendor/dep": "^2.0"}}]}
        lock = tmp_path / "composer.lock"
        lock.write_text(json.dumps(data))
        items = m.parse_composer_lock(lock)
        assert len(items) == 1
        deps = items[0][2]
        assert "php" not in deps
        assert "ext-json" not in deps
        assert "lib-curl" not in deps
        assert "vendor/dep" in deps


# ─────────────────────────────────────────────────────────────────────────────
# parse_go_mod
# ─────────────────────────────────────────────────────────────────────────────
class TestParseGoMod:
    def setup_method(self):
        self.items = m.parse_go_mod(FIXTURES / "go.mod")

    def test_returns_list(self):
        assert isinstance(self.items, list)

    def test_non_empty(self):
        assert len(self.items) > 0

    def test_each_item_is_triple(self):
        for item in self.items:
            assert len(item) == 3

    def test_contains_gin(self):
        assert any("gin" in n for n in names(self.items))

    def test_contains_testify(self):
        assert any("testify" in n for n in names(self.items))

    def test_gin_version(self):
        assert version_of(self.items, "gin") == "1.9.1"

    def test_testify_version(self):
        assert version_of(self.items, "testify") == "1.8.4"

    def test_golang_crypto_version(self):
        assert version_of(self.items, "crypto") == "0.14.0"

    def test_deps_empty_for_go_mod(self):
        # go.mod gives only direct deps; dep graph per module unavailable
        for _, _, deps in self.items:
            assert deps == []

    def test_indirect_included(self):
        # indirect deps in require block should also be captured
        assert any("bytedance" in n or "sonic" in n for n in names(self.items))


# ─────────────────────────────────────────────────────────────────────────────
# parse_gemfile_lock
# ─────────────────────────────────────────────────────────────────────────────
class TestParseGemfileLock:
    def setup_method(self):
        self.items = m.parse_gemfile_lock(FIXTURES / "Gemfile.lock")

    def test_returns_list(self):
        assert isinstance(self.items, list)

    def test_non_empty(self):
        assert len(self.items) > 0

    def test_each_item_is_triple(self):
        for item in self.items:
            assert len(item) == 3

    def test_contains_rails(self):
        assert any("rails" in n.lower() for n in names(self.items))

    def test_contains_activesupport(self):
        assert any("activesupport" in n.lower() for n in names(self.items))

    def test_rails_version(self):
        assert version_of(self.items, "rails") == "7.0.4"

    def test_activesupport_version(self):
        assert version_of(self.items, "activesupport") == "7.0.4"

    def test_activesupport_deps_include_concurrent_ruby(self):
        deps = deps_of(self.items, "activesupport")
        assert deps is not None
        assert any("concurrent-ruby" in d for d in deps)

    def test_contains_concurrent_ruby(self):
        assert any("concurrent-ruby" in n.lower() for n in names(self.items))
