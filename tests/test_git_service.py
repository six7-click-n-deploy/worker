"""Tests for Git service."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from app.services.git_service import GitService


@pytest.fixture
def git_service(tmp_path):
    """Create GitService instance with temporary base path."""
    with patch('app.services.git_service.settings') as mock_settings:
        mock_settings.TEMP_REPO_BASE_PATH = str(tmp_path)
        mock_settings.GIT_ACCESS_TOKEN = "test-token-123"
        service = GitService()
        return service


class TestGitServiceURLParsing:
    """Test URL parsing functionality."""
    
    def test_parse_ssh_url(self, git_service):
        """Test parsing SSH Git URL."""
        url = "git@github.com:owner/repo.git"
        result = git_service._parse_git_url(url)
        
        assert result is not None
        assert result['host'] == 'github.com'
        assert result['owner'] == 'owner'
        assert result['repo'] == 'repo'
    
    def test_parse_https_url(self, git_service):
        """Test parsing HTTPS Git URL."""
        url = "https://github.com/owner/repo.git"
        result = git_service._parse_git_url(url)
        
        assert result is not None
        assert result['host'] == 'github.com'
        assert result['owner'] == 'owner'
        assert result['repo'] == 'repo'
    
    def test_parse_url_without_git_extension(self, git_service):
        """Test parsing URL without .git extension."""
        url = "https://gitlab.com/group/project"
        result = git_service._parse_git_url(url)
        
        assert result is not None
        assert result['host'] == 'gitlab.com'
        assert result['owner'] == 'group'
        assert result['repo'] == 'project'
    
    def test_parse_invalid_url(self, git_service):
        """Test parsing invalid URL returns None."""
        url = "not-a-valid-git-url"
        result = git_service._parse_git_url(url)
        
        assert result is None


class TestGitServiceAuthentication:
    """Test authentication URL generation."""
    
    def test_convert_ssh_to_authenticated_https(self, git_service):
        """Test converting SSH URL to authenticated HTTPS."""
        ssh_url = "git@github.com:owner/repo.git"
        result = git_service._get_authenticated_url(ssh_url)
        
        assert result.startswith("https://test-token-123@")
        assert "github.com/owner/repo.git" in result
    
    def test_add_token_to_https_url(self, git_service):
        """Test adding token to HTTPS URL."""
        https_url = "https://github.com/owner/repo.git"
        result = git_service._get_authenticated_url(https_url)
        
        assert "test-token-123@github.com" in result
    
    def test_authenticated_url_format(self, git_service):
        """Test authenticated URL has correct format."""
        url = "git@gitlab.com:mygroup/myrepo.git"
        result = git_service._get_authenticated_url(url)
        
        assert result.startswith("https://")
        assert "@gitlab.com" in result


class TestGitServiceCloning:
    """Test repository cloning functionality."""
    
    @patch('app.services.git_service.git.Repo.clone_from')
    def test_clone_release_success(self, mock_clone, git_service, tmp_path):
        """Test successful repository cloning."""
        mock_repo = MagicMock()
        mock_repo.head.commit.hexsha = "abc123def456"
        mock_clone.return_value = mock_repo
        
        git_url = "https://github.com/owner/repo.git"
        tag = "v1.0.0"
        deployment_id = "test-deploy-123"
        
        result = git_service.clone_release(git_url, tag, deployment_id)
        
        assert result is not None
        assert "deploy_test-deploy-123" in result
        mock_clone.assert_called_once()
        
        # Verify clone parameters
        call_args = mock_clone.call_args
        assert call_args.kwargs['branch'] == tag
        assert call_args.kwargs['depth'] == 1
        assert call_args.kwargs['single_branch'] is True
    
    @patch('app.services.git_service.git.Repo.clone_from')
    def test_clone_release_removes_existing_directory(self, mock_clone, git_service, tmp_path):
        """Test that existing directory is removed before cloning."""
        mock_repo = MagicMock()
        mock_repo.head.commit.hexsha = "abc123"
        mock_clone.return_value = mock_repo
        
        deployment_id = "test-deploy-456"
        existing_path = tmp_path / f"deploy_{deployment_id}"
        existing_path.mkdir(parents=True)
        
        # Verify directory exists before cloning
        assert existing_path.exists()
        
        git_service.clone_release("https://github.com/test/repo.git", "v1.0.0", deployment_id)
        
        # Should have been removed and recreated
        mock_clone.assert_called_once()
    
    @patch('app.services.git_service.git.Repo.clone_from')
    def test_clone_release_failure_cleanup(self, mock_clone, git_service, tmp_path):
        """Test cleanup on clone failure."""
        mock_clone.side_effect = Exception("Clone failed")
        
        with pytest.raises(Exception, match="Failed to clone"):
            git_service.clone_release("https://github.com/test/repo.git", "v1.0.0", "test-fail")
        
        # Verify directory was cleaned up
        fail_path = tmp_path / "deploy_test-fail"
        assert not fail_path.exists()


class TestGitServiceCleanup:
    """Test repository cleanup functionality."""
    
    def test_cleanup_existing_directory(self, git_service, tmp_path):
        """Test cleanup of existing directory."""
        test_dir = tmp_path / "test_cleanup"
        test_dir.mkdir(parents=True)
        (test_dir / "test_file.txt").write_text("test content")
        
        assert test_dir.exists()
        
        git_service.cleanup_repository(str(test_dir))
        
        assert not test_dir.exists()
    
    def test_cleanup_nonexistent_directory(self, git_service, tmp_path):
        """Test cleanup handles nonexistent directory gracefully."""
        nonexistent = tmp_path / "does_not_exist"
        
        # Should not raise an exception
        git_service.cleanup_repository(str(nonexistent))
        
        assert not nonexistent.exists()


@pytest.mark.integration
class TestGitServiceIntegration:
    """Integration tests (require actual Git access)."""
    
    @pytest.mark.skip(reason="Requires actual Git repository access")
    def test_clone_real_repository(self, git_service):
        """Test cloning a real public repository."""
        # This test would require a real public repository
        # Skip by default to avoid external dependencies
        pass
