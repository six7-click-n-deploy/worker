"""Tests for the Packer template discovery service.

Covers ``_discover_packer_templates`` against synthetic directory layouts
under ``tmp_path``: legacy layout, multi-template layout, missing/empty
``packer/`` directory, bad keys, and the legacy+multi conflict.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.services.packer_discovery import PackerTemplateDiscoveryError, _discover_packer_templates, _PackerTemplate

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _make_legacy_layout(root: Path, *, with_variables: bool = False) -> None:
    """Create ``packer/template.pkr.hcl`` (and optionally ``variables.pkr.hcl``)."""
    packer = root / "packer"
    packer.mkdir(parents=True, exist_ok=True)
    (packer / "template.pkr.hcl").write_text("# legacy template")
    if with_variables:
        (packer / "variables.pkr.hcl").write_text("# legacy variables")


def _make_subdir_template(root: Path, key: str, *, with_variables: bool = False) -> None:
    """Create ``packer/<key>/template.pkr.hcl`` under ``root``."""
    sub = root / "packer" / key
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "template.pkr.hcl").write_text(f"# template for {key}")
    if with_variables:
        (sub / "variables.pkr.hcl").write_text(f"# variables for {key}")


class TestNoTemplates:
    """Empty / missing ``packer/`` directory behaviour."""

    def test_returns_empty_list_when_packer_dir_missing(self, tmp_path):
        """A repo without a ``packer/`` directory yields an empty list."""
        result = _discover_packer_templates(str(tmp_path))
        assert result == []

    def test_returns_empty_list_when_packer_dir_empty(self, tmp_path):
        """An empty ``packer/`` directory yields an empty list."""
        (tmp_path / "packer").mkdir()
        result = _discover_packer_templates(str(tmp_path))
        assert result == []

    def test_returns_empty_when_packer_path_is_a_file(self, tmp_path):
        """A ``packer`` regular file (not a directory) is treated as missing."""
        (tmp_path / "packer").write_text("not a dir")
        result = _discover_packer_templates(str(tmp_path))
        assert result == []

    def test_subdirectory_without_template_is_ignored(self, tmp_path):
        """A subdir lacking ``template.pkr.hcl`` is silently skipped."""
        helpers = tmp_path / "packer" / "_common"
        helpers.mkdir(parents=True)
        (helpers / "scripts.sh").write_text("# shared script")
        result = _discover_packer_templates(str(tmp_path))
        assert result == []

    def test_files_at_packer_root_other_than_template_are_ignored(self, tmp_path):
        """Stray files at ``packer/`` root that are not the legacy template are ignored."""
        packer = tmp_path / "packer"
        packer.mkdir()
        (packer / "README.md").write_text("docs")
        (packer / "variables.pkr.hcl").write_text("vars only, no template")
        result = _discover_packer_templates(str(tmp_path))
        assert result == []


class TestLegacyLayout:
    """Legacy single-template layout (``packer/template.pkr.hcl``)."""

    def test_legacy_layout_returns_single_default_template(self, tmp_path):
        """A legacy ``packer/template.pkr.hcl`` yields one template with key='default'."""
        _make_legacy_layout(tmp_path, with_variables=True)

        result = _discover_packer_templates(str(tmp_path))

        assert len(result) == 1
        tmpl = result[0]
        assert isinstance(tmpl, _PackerTemplate)
        assert tmpl.key == "default"
        assert tmpl.template_path == str(tmp_path / "packer" / "template.pkr.hcl")
        assert tmpl.variables_path == str(tmp_path / "packer" / "variables.pkr.hcl")

    def test_legacy_layout_returns_variables_path_even_when_missing(self, tmp_path):
        """``variables_path`` is returned even when ``variables.pkr.hcl`` does not exist."""
        _make_legacy_layout(tmp_path, with_variables=False)

        result = _discover_packer_templates(str(tmp_path))

        assert len(result) == 1
        tmpl = result[0]
        assert tmpl.variables_path == str(tmp_path / "packer" / "variables.pkr.hcl")
        assert not os.path.isfile(tmpl.variables_path)

    def test_legacy_layout_with_helper_subdir_without_template_is_still_legacy(self, tmp_path):
        """A helper subdir without ``template.pkr.hcl`` does not turn it into multi-mode."""
        _make_legacy_layout(tmp_path)
        helpers = tmp_path / "packer" / "_common"
        helpers.mkdir()
        (helpers / "shared.sh").write_text("# shared")

        result = _discover_packer_templates(str(tmp_path))

        assert len(result) == 1
        assert result[0].key == "default"


class TestMultiTemplateLayout:
    """Multi-template subdirectory layout (``packer/<key>/template.pkr.hcl``)."""

    def test_multi_layout_returns_one_template_per_subdir(self, tmp_path):
        """Two valid subdirs each with ``template.pkr.hcl`` produce two templates."""
        _make_subdir_template(tmp_path, "web", with_variables=True)
        _make_subdir_template(tmp_path, "db", with_variables=False)

        result = _discover_packer_templates(str(tmp_path))

        assert len(result) == 2
        keys = [t.key for t in result]
        assert keys == ["db", "web"]  # alphabetical

    def test_multi_layout_sorted_alphabetically(self, tmp_path):
        """Templates are returned sorted alphabetically by key."""
        for key in ["zeta", "alpha", "mid_2", "beta-1"]:
            _make_subdir_template(tmp_path, key)

        result = _discover_packer_templates(str(tmp_path))

        assert [t.key == k for t, k in zip(result, sorted(["zeta", "alpha", "mid_2", "beta-1"]), strict=False)]
        assert [t.key for t in result] == sorted(["zeta", "alpha", "mid_2", "beta-1"])

    def test_multi_layout_paths_are_absolute_under_subdir(self, tmp_path):
        """Each returned template has paths under its subdirectory."""
        _make_subdir_template(tmp_path, "web", with_variables=True)

        result = _discover_packer_templates(str(tmp_path))

        assert len(result) == 1
        tmpl = result[0]
        assert tmpl.key == "web"
        assert tmpl.template_path == str(tmp_path / "packer" / "web" / "template.pkr.hcl")
        assert tmpl.variables_path == str(tmp_path / "packer" / "web" / "variables.pkr.hcl")

    def test_multi_layout_variables_path_returned_even_when_missing(self, tmp_path):
        """``variables_path`` is returned in multi mode even when the file is absent."""
        _make_subdir_template(tmp_path, "web", with_variables=False)

        result = _discover_packer_templates(str(tmp_path))

        assert len(result) == 1
        tmpl = result[0]
        assert tmpl.variables_path.endswith("/web/variables.pkr.hcl")
        assert not os.path.isfile(tmpl.variables_path)

    def test_subdirectory_without_template_silently_ignored(self, tmp_path):
        """A sibling subdir without ``template.pkr.hcl`` is dropped, not an error."""
        _make_subdir_template(tmp_path, "web")
        # Helper subdir with no template:
        helper = tmp_path / "packer" / "_common"
        helper.mkdir()
        (helper / "scripts.sh").write_text("# shared")
        # Another empty subdir:
        (tmp_path / "packer" / "scripts").mkdir()

        result = _discover_packer_templates(str(tmp_path))

        assert [t.key for t in result] == ["web"]

    def test_stable_sort_repeated_calls_return_same_order(self, tmp_path):
        """Repeated discovery on the same layout returns identical ordered lists."""
        for key in ["c_app", "a_app", "b_app"]:
            _make_subdir_template(tmp_path, key)

        first = _discover_packer_templates(str(tmp_path))
        second = _discover_packer_templates(str(tmp_path))
        third = _discover_packer_templates(str(tmp_path))

        assert [t.key for t in first] == ["a_app", "b_app", "c_app"]
        assert [t.key for t in first] == [t.key for t in second] == [t.key for t in third]
        assert [t.template_path for t in first] == [t.template_path for t in second]


class TestBadKeys:
    """Subdirectory names that violate the ``[a-z][a-z0-9_-]{0,30}`` rule."""

    def test_uppercase_subdir_name_raises(self, tmp_path):
        """An uppercase key triggers ``PackerTemplateDiscoveryError``."""
        _make_subdir_template(tmp_path, "Web")

        with pytest.raises(PackerTemplateDiscoveryError) as excinfo:
            _discover_packer_templates(str(tmp_path))

        assert "Web" in str(excinfo.value)
        assert "invalid keys" in str(excinfo.value)

    def test_hyphen_first_subdir_name_raises(self, tmp_path):
        """A key starting with a hyphen triggers ``PackerTemplateDiscoveryError``."""
        _make_subdir_template(tmp_path, "-web")

        with pytest.raises(PackerTemplateDiscoveryError) as excinfo:
            _discover_packer_templates(str(tmp_path))

        assert "-web" in str(excinfo.value)

    def test_subdir_name_longer_than_31_chars_raises(self, tmp_path):
        """A key with more than 31 characters triggers ``PackerTemplateDiscoveryError``."""
        too_long = "a" + "b" * 31  # 32 chars
        assert len(too_long) == 32
        _make_subdir_template(tmp_path, too_long)

        with pytest.raises(PackerTemplateDiscoveryError) as excinfo:
            _discover_packer_templates(str(tmp_path))

        assert too_long in str(excinfo.value)

    def test_subdir_name_with_dot_raises(self, tmp_path):
        """A key containing a dot triggers ``PackerTemplateDiscoveryError``."""
        _make_subdir_template(tmp_path, "web.app")

        with pytest.raises(PackerTemplateDiscoveryError) as excinfo:
            _discover_packer_templates(str(tmp_path))

        assert "web.app" in str(excinfo.value)

    def test_subdir_name_with_digit_first_raises(self, tmp_path):
        """A key starting with a digit triggers ``PackerTemplateDiscoveryError``."""
        _make_subdir_template(tmp_path, "1web")

        with pytest.raises(PackerTemplateDiscoveryError) as excinfo:
            _discover_packer_templates(str(tmp_path))

        assert "1web" in str(excinfo.value)

    def test_multiple_bad_keys_all_listed(self, tmp_path):
        """All invalid keys are surfaced together in the error message."""
        _make_subdir_template(tmp_path, "Web")
        _make_subdir_template(tmp_path, "-db")
        _make_subdir_template(tmp_path, "ok_one")  # valid; should not appear

        with pytest.raises(PackerTemplateDiscoveryError) as excinfo:
            _discover_packer_templates(str(tmp_path))

        msg = str(excinfo.value)
        assert "Web" in msg
        assert "-db" in msg

    def test_max_length_31_chars_is_valid(self, tmp_path):
        """A key of exactly 31 characters is accepted (boundary)."""
        valid = "a" + "b" * 30  # 31 chars total
        assert len(valid) == 31
        _make_subdir_template(tmp_path, valid)

        result = _discover_packer_templates(str(tmp_path))

        assert len(result) == 1
        assert result[0].key == valid


class TestLegacyAndMultiConflict:
    """Mixing legacy file with multi subdirectories must be rejected."""

    def test_legacy_plus_multi_subdirs_raises(self, tmp_path):
        """Legacy template plus subdir templates raises ``PackerTemplateDiscoveryError``."""
        _make_legacy_layout(tmp_path)
        _make_subdir_template(tmp_path, "web")
        _make_subdir_template(tmp_path, "db")

        with pytest.raises(PackerTemplateDiscoveryError) as excinfo:
            _discover_packer_templates(str(tmp_path))

        msg = str(excinfo.value)
        assert "BOTH" in msg
        assert "web" in msg
        assert "db" in msg

    def test_legacy_plus_helper_subdir_without_template_is_legacy(self, tmp_path):
        """A helper subdir without ``template.pkr.hcl`` does not trigger the conflict."""
        _make_legacy_layout(tmp_path)
        helper = tmp_path / "packer" / "_common"
        helper.mkdir()
        (helper / "shared.sh").write_text("# shared")

        result = _discover_packer_templates(str(tmp_path))

        assert len(result) == 1
        assert result[0].key == "default"

    def test_bad_keys_take_precedence_over_legacy_conflict(self, tmp_path):
        """When bad keys exist alongside legacy file, the bad-keys error is raised first."""
        _make_legacy_layout(tmp_path)
        _make_subdir_template(tmp_path, "Web")  # bad key

        with pytest.raises(PackerTemplateDiscoveryError) as excinfo:
            _discover_packer_templates(str(tmp_path))

        # Bad-keys branch fires before the legacy+multi conflict branch.
        assert "invalid keys" in str(excinfo.value)
