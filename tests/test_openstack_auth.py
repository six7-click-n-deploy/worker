"""Tests for the PerTaskCloudsConfig context manager."""

from __future__ import annotations

import os
import stat
from typing import Any

import pytest
import yaml

from app.services.openstack_auth import CredentialEnvelopeError, PerTaskCloudsConfig
from app.utils.crypto import encrypt_b64


def _make_envelope(
    *,
    identifier: str = "test-app-cred-id",
    secret: str = "test-app-cred-secret",
    auth_type: str = "v3applicationcredential",
    auth_url: str = "https://keystone.example.com/v3",
    **extra: Any,
) -> dict[str, Any]:
    """Build a valid envelope using the same Fernet key the worker uses."""
    env: dict[str, Any] = {
        "encrypted_identifier_b64": encrypt_b64(identifier),
        "encrypted_secret_b64": encrypt_b64(secret),
        "auth_type": auth_type,
        "auth_url": auth_url,
    }
    env.update(extra)
    return env


@pytest.mark.unit
class TestEnvelopeValidation:
    """Validation of the dispatched envelope dict."""

    def test_none_envelope_raises_credential_envelope_error(self, tmp_path):
        """A None envelope must raise CredentialEnvelopeError, not TypeError."""
        with pytest.raises(CredentialEnvelopeError, match="missing or invalid"):
            PerTaskCloudsConfig(None, work_dir=str(tmp_path))  # type: ignore[arg-type]

    def test_non_dict_envelope_raises_credential_envelope_error(self, tmp_path):
        """A non-dict envelope (e.g. a list) must raise CredentialEnvelopeError."""
        with pytest.raises(CredentialEnvelopeError, match="missing or invalid"):
            PerTaskCloudsConfig(["not", "a", "dict"], work_dir=str(tmp_path))  # type: ignore[arg-type]

    def test_string_envelope_raises_credential_envelope_error(self, tmp_path):
        """A string envelope must raise CredentialEnvelopeError."""
        with pytest.raises(CredentialEnvelopeError, match="missing or invalid"):
            PerTaskCloudsConfig("not-a-dict", work_dir=str(tmp_path))  # type: ignore[arg-type]

    def test_missing_encrypted_identifier_raises(self, tmp_path):
        """Envelope without encrypted_identifier_b64 must raise with the field name."""
        envelope = _make_envelope()
        del envelope["encrypted_identifier_b64"]
        with pytest.raises(CredentialEnvelopeError, match="encrypted_identifier_b64"):
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))

    def test_missing_encrypted_secret_raises(self, tmp_path):
        """Envelope without encrypted_secret_b64 must raise with the field name."""
        envelope = _make_envelope()
        del envelope["encrypted_secret_b64"]
        with pytest.raises(CredentialEnvelopeError, match="encrypted_secret_b64"):
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))

    def test_missing_auth_type_raises(self, tmp_path):
        """Envelope without auth_type must raise with the field name."""
        envelope = _make_envelope()
        del envelope["auth_type"]
        with pytest.raises(CredentialEnvelopeError, match="auth_type"):
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))

    def test_missing_auth_url_raises(self, tmp_path):
        """Envelope without auth_url must raise with the field name."""
        envelope = _make_envelope()
        del envelope["auth_url"]
        with pytest.raises(CredentialEnvelopeError, match="auth_url"):
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))

    def test_garbage_b64_identifier_raises_with_clean_message(self, tmp_path):
        """A non-decryptable identifier must surface the exception type, not the text."""
        envelope = _make_envelope()
        envelope["encrypted_identifier_b64"] = "bm90LWEtdmFsaWQtZmVybmV0LXRva2Vu"  # base64 of garbage
        with pytest.raises(CredentialEnvelopeError, match="Failed to decrypt"):
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))

    def test_garbage_b64_secret_raises_with_clean_message(self, tmp_path):
        """A non-decryptable secret must surface the exception type, not the text."""
        envelope = _make_envelope()
        envelope["encrypted_secret_b64"] = "bm90LWEtdmFsaWQtZmVybmV0LXRva2Vu"
        with pytest.raises(CredentialEnvelopeError, match="Failed to decrypt"):
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))

    def test_non_base64_identifier_raises_credential_envelope_error(self, tmp_path):
        """Identifier that isn't valid base64 must still be wrapped as CredentialEnvelopeError."""
        envelope = _make_envelope()
        envelope["encrypted_identifier_b64"] = "!!!not-base64@@@"
        with pytest.raises(CredentialEnvelopeError, match="Failed to decrypt"):
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))

    def test_decrypt_error_message_mentions_shared_key(self, tmp_path):
        """The decrypt-failure message should hint at the shared CREDENTIAL_ENCRYPTION_KEY."""
        envelope = _make_envelope()
        envelope["encrypted_identifier_b64"] = "bm90LWEtdmFsaWQtZmVybmV0LXRva2Vu"
        with pytest.raises(CredentialEnvelopeError, match="CREDENTIAL_ENCRYPTION_KEY"):
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))


@pytest.mark.unit
class TestApplicationCredentialFlow:
    """Happy path for v3applicationcredential auth."""

    def test_clouds_yaml_is_written_under_work_dir(self, tmp_path):
        """__enter__ must create clouds.yaml at <work_dir>/clouds.yaml."""
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))
        with cfg:
            assert os.path.isfile(cfg.path)
            assert cfg.path == os.path.join(str(tmp_path), "clouds.yaml")

    def test_clouds_yaml_has_mode_0600(self, tmp_path):
        """The clouds.yaml file must have permission bits exactly 0o600."""
        envelope = _make_envelope()
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as _:
            mode = stat.S_IMODE(os.stat(os.path.join(str(tmp_path), "clouds.yaml")).st_mode)
            assert mode == 0o600

    def test_clouds_yaml_contents_for_application_credential(self, tmp_path):
        """clouds.yaml must contain clouds.openstack with application_credential_id/_secret."""
        envelope = _make_envelope(
            identifier="my-app-id",
            secret="my-app-secret",
        )
        with (
            PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as _,
            open(os.path.join(str(tmp_path), "clouds.yaml")) as f,
        ):
            doc = yaml.safe_load(f)

        cloud = doc["clouds"]["openstack"]
        assert cloud["auth_type"] == "v3applicationcredential"
        assert cloud["auth"]["application_credential_id"] == "my-app-id"
        assert cloud["auth"]["application_credential_secret"] == "my-app-secret"
        assert cloud["auth"]["auth_url"] == "https://keystone.example.com/v3"
        # Password-only keys must not leak into app-cred auth block
        assert "username" not in cloud["auth"]
        assert "password" not in cloud["auth"]

    def test_env_vars_for_application_credential(self, tmp_path):
        """The env dict for v3applicationcredential must expose all expected OS_* keys."""
        envelope = _make_envelope(
            identifier="app-id-1",
            secret="app-secret-1",
            auth_url="https://keystone.example.com/v3",
        )
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as env:
            assert env["OS_CLIENT_CONFIG_FILE"] == os.path.join(str(tmp_path), "clouds.yaml")
            assert env["OS_CLOUD"] == "openstack"
            assert env["OS_AUTH_TYPE"] == "v3applicationcredential"
            assert env["OS_APPLICATION_CREDENTIAL_ID"] == "app-id-1"
            assert env["OS_APPLICATION_CREDENTIAL_SECRET"] == "app-secret-1"
            assert env["OS_AUTH_URL"] == "https://keystone.example.com/v3"
            assert env["OS_INTERFACE"] == "public"
            assert env["OS_IDENTITY_API_VERSION"] == "3"
            # Password keys must NOT be present
            assert "OS_USERNAME" not in env
            assert "OS_PASSWORD" not in env

    def test_region_name_passes_through_when_provided(self, tmp_path):
        """When region_name is in the envelope, both yaml and env include it."""
        envelope = _make_envelope(region_name="RegionOne")
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as env:
            with open(env["OS_CLIENT_CONFIG_FILE"]) as f:
                doc = yaml.safe_load(f)
            assert doc["clouds"]["openstack"]["region_name"] == "RegionOne"
            assert env["OS_REGION_NAME"] == "RegionOne"

    def test_region_name_omitted_when_absent(self, tmp_path):
        """No region_name in envelope -> no region_name key in yaml or env."""
        envelope = _make_envelope()
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as env:
            with open(env["OS_CLIENT_CONFIG_FILE"]) as f:
                doc = yaml.safe_load(f)
            assert "region_name" not in doc["clouds"]["openstack"]
            assert "OS_REGION_NAME" not in env

    def test_custom_interface_and_api_version_override_defaults(self, tmp_path):
        """Explicit interface and identity_api_version in envelope override defaults."""
        envelope = _make_envelope(interface="internal", identity_api_version="3.14")
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as env:
            assert env["OS_INTERFACE"] == "internal"
            assert env["OS_IDENTITY_API_VERSION"] == "3.14"

    def test_work_dir_is_created_if_missing(self, tmp_path):
        """A non-existent work_dir must be created during __init__."""
        sub = tmp_path / "deep" / "nested" / "work"
        assert not sub.exists()
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(sub))
        assert sub.is_dir()
        with cfg:
            assert os.path.isfile(cfg.path)


@pytest.mark.unit
class TestPasswordAuthFlow:
    """Happy path for password (non-v3applicationcredential) auth."""

    def test_password_env_vars_present(self, tmp_path):
        """Password auth must populate OS_USERNAME/OS_PASSWORD/OS_PROJECT_* and OS_AUTH_TYPE=password."""
        envelope = _make_envelope(
            identifier="alice",
            secret="hunter2",
            auth_type="password",
            project_id="proj-uuid",
            project_name="proj-name",
            user_domain_name="Default",
            project_domain_name="Default",
        )
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as env:
            assert env["OS_AUTH_TYPE"] == "password"
            assert env["OS_USERNAME"] == "alice"
            assert env["OS_PASSWORD"] == "hunter2"
            assert env["OS_PROJECT_ID"] == "proj-uuid"
            assert env["OS_PROJECT_NAME"] == "proj-name"
            assert env["OS_USER_DOMAIN_NAME"] == "Default"
            assert env["OS_PROJECT_DOMAIN_NAME"] == "Default"
            # Application credential keys must NOT leak in
            assert "OS_APPLICATION_CREDENTIAL_ID" not in env
            assert "OS_APPLICATION_CREDENTIAL_SECRET" not in env

    def test_password_clouds_yaml_contents(self, tmp_path):
        """clouds.yaml for password auth must hold username/password and project info under auth."""
        envelope = _make_envelope(
            identifier="bob",
            secret="pw",
            auth_type="password",
            project_id="pid",
            project_name="pname",
            user_domain_name="Default",
            project_domain_name="Default",
        )
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as env, open(env["OS_CLIENT_CONFIG_FILE"]) as f:
            doc = yaml.safe_load(f)
        auth = doc["clouds"]["openstack"]["auth"]
        assert auth["username"] == "bob"
        assert auth["password"] == "pw"
        assert auth["project_id"] == "pid"
        assert auth["project_name"] == "pname"
        assert auth["user_domain_name"] == "Default"
        assert auth["project_domain_name"] == "Default"
        # Application-credential keys must not appear
        assert "application_credential_id" not in auth
        assert "application_credential_secret" not in auth

    def test_project_domain_defaults_to_user_domain_when_only_user_domain_given(self, tmp_path):
        """If only user_domain_name is supplied, project_domain_name must mirror it."""
        envelope = _make_envelope(
            auth_type="password",
            user_domain_name="MyDomain",
            # project_domain_name intentionally omitted
        )
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as env:
            assert env["OS_USER_DOMAIN_NAME"] == "MyDomain"
            assert env["OS_PROJECT_DOMAIN_NAME"] == "MyDomain"
            with open(env["OS_CLIENT_CONFIG_FILE"]) as f:
                doc = yaml.safe_load(f)
            auth = doc["clouds"]["openstack"]["auth"]
            assert auth["user_domain_name"] == "MyDomain"
            assert auth["project_domain_name"] == "MyDomain"

    def test_password_optional_fields_omitted_when_absent(self, tmp_path):
        """When project_id/name and domains are absent, neither env nor yaml include them."""
        envelope = _make_envelope(auth_type="password")
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as env:
            assert "OS_PROJECT_ID" not in env
            assert "OS_PROJECT_NAME" not in env
            assert "OS_USER_DOMAIN_NAME" not in env
            assert "OS_PROJECT_DOMAIN_NAME" not in env
            with open(env["OS_CLIENT_CONFIG_FILE"]) as f:
                doc = yaml.safe_load(f)
            auth = doc["clouds"]["openstack"]["auth"]
            assert "project_id" not in auth
            assert "project_name" not in auth
            assert "user_domain_name" not in auth
            assert "project_domain_name" not in auth


@pytest.mark.unit
class TestStaleFileAndRecovery:
    """File-system robustness: stale files and partial writes."""

    def test_stale_clouds_yaml_is_cleaned_and_rewritten(self, tmp_path):
        """A pre-existing clouds.yaml from a crashed prior task must be replaced."""
        stale_path = tmp_path / "clouds.yaml"
        stale_path.write_text("stale: leftover\n")
        assert stale_path.read_text() == "stale: leftover\n"

        envelope = _make_envelope(identifier="fresh-id", secret="fresh-secret")
        with PerTaskCloudsConfig(envelope, work_dir=str(tmp_path)) as _:
            with open(stale_path) as f:
                doc = yaml.safe_load(f)
            assert "stale" not in doc
            assert doc["clouds"]["openstack"]["auth"]["application_credential_id"] == "fresh-id"
            # And the new file is also mode 0600
            mode = stat.S_IMODE(os.stat(stale_path).st_mode)
            assert mode == 0o600

    def test_partial_write_failure_removes_file(self, tmp_path, mocker):
        """If yaml.safe_dump raises mid-write, the partial file must not remain on disk."""
        mocker.patch(
            "app.services.openstack_auth.yaml.safe_dump",
            side_effect=RuntimeError("boom"),
        )
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))
        with pytest.raises(RuntimeError, match="boom"):
            cfg.__enter__()
        # The empty/partial file created via os.open must have been removed.
        assert not os.path.exists(cfg.path)

    def test_partial_write_failure_when_stale_file_present(self, tmp_path, mocker):
        """Recovery branch (FileExistsError) must also clean up its own partial write."""
        (tmp_path / "clouds.yaml").write_text("stale\n")
        mocker.patch(
            "app.services.openstack_auth.yaml.safe_dump",
            side_effect=RuntimeError("boom"),
        )
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))
        with pytest.raises(RuntimeError, match="boom"):
            cfg.__enter__()
        assert not os.path.exists(cfg.path)


@pytest.mark.unit
class TestExitAndShredding:
    """__exit__ shreds plaintext and the file."""

    def test_exit_removes_clouds_yaml(self, tmp_path):
        """After exiting the context, clouds.yaml must be gone from disk."""
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))
        with cfg:
            assert os.path.isfile(cfg.path)
        assert not os.path.exists(cfg.path)

    def test_exit_is_idempotent_when_file_already_gone(self, tmp_path):
        """If the file disappears before __exit__, __exit__ must not raise."""
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))
        cfg.__enter__()
        os.remove(cfg.path)
        # Should swallow FileNotFoundError silently.
        cfg.__exit__(None, None, None)
        assert cfg._creds is None

    def test_cloud_block_raises_after_shred(self, tmp_path):
        """Calling _cloud_block after __exit__ must raise 'already shredded'."""
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))
        with cfg:
            pass
        with pytest.raises(CredentialEnvelopeError, match="already shredded"):
            cfg._cloud_block()

    def test_creds_attribute_is_cleared_after_exit(self, tmp_path):
        """The private _creds attribute must be set to None after __exit__."""
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))
        with cfg:
            assert cfg._creds is not None
        assert cfg._creds is None

    def test_path_attribute_survives_shred_for_logging(self, tmp_path):
        """The .path attribute must remain readable after shred (so callers can log it)."""
        envelope = _make_envelope()
        cfg = PerTaskCloudsConfig(envelope, work_dir=str(tmp_path))
        expected_path = os.path.join(str(tmp_path), "clouds.yaml")
        with cfg:
            pass
        assert cfg.path == expected_path
