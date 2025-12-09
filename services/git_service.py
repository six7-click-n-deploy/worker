"""
Git service for cloning repositories temporarily
"""
import os
import shutil
import logging
from pathlib import Path
from typing import Optional
import git
from config import settings

logger = logging.getLogger(__name__)

class GitService:
    """Service for handling Git operations"""
    
    def __init__(self):
        self.base_path = settings.TEMP_REPO_BASE_PATH
        
    def clone_repository(self, git_url: str, deployment_id: str, commit_hash: Optional[str] = None) -> str:
        """
        Clone a repository to a temporary directory
        
        Args:
            git_url: URL of the Git repository
            deployment_id: Unique deployment ID for folder naming
            commit_hash: Optional specific commit to checkout
            
        Returns:
            str: Path to the cloned repository
        """
        # Create unique temporary directory for this deployment
        repo_path = os.path.join(self.base_path, f"deploy_{deployment_id}")
        
        # Clean up if directory exists from previous failed attempt
        if os.path.exists(repo_path):
            logger.warning(f"Repository path {repo_path} already exists, cleaning up...")
            shutil.rmtree(repo_path)
        
        try:
            logger.info(f"Cloning repository {git_url} to {repo_path}")
            repo = git.Repo.clone_from(git_url, repo_path)
            
            # Checkout specific commit if provided
            if commit_hash:
                logger.info(f"Checking out commit {commit_hash}")
                repo.git.checkout(commit_hash)
            
            logger.info(f"Successfully cloned repository to {repo_path}")
            return repo_path
            
        except git.GitCommandError as e:
            logger.error(f"Git clone failed: {e}")
            # Clean up on failure
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
            raise Exception(f"Failed to clone repository: {str(e)}")
    
    def cleanup_repository(self, repo_path: str) -> None:
        """
        Delete the cloned repository
        
        Args:
            repo_path: Path to the repository to delete
        """
        try:
            if os.path.exists(repo_path):
                logger.info(f"Cleaning up repository at {repo_path}")
                shutil.rmtree(repo_path)
                logger.info(f"Successfully cleaned up {repo_path}")
            else:
                logger.warning(f"Repository path {repo_path} does not exist")
        except Exception as e:
            logger.error(f"Failed to cleanup repository at {repo_path}: {e}")
            # Don't raise exception - cleanup is best effort
    
    def get_latest_commit_info(self, repo_path: str) -> dict:
        """
        Get information about the current commit
        
        Args:
            repo_path: Path to the repository
            
        Returns:
            dict: Commit information (hash, message, author, date)
        """
        try:
            repo = git.Repo(repo_path)
            commit = repo.head.commit
            
            return {
                "hash": commit.hexsha,
                "message": commit.message.strip(),
                "author": str(commit.author),
                "date": commit.committed_datetime.isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get commit info: {e}")
            return {}

# Singleton instance
git_service = GitService()
