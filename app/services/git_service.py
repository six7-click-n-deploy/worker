import os
import shutil
import logging
from typing import Optional
import git
from ..config import settings

logger = logging.getLogger(__name__)

class GitService:
    def __init__(self):
        self.base_path = settings.TEMP_REPO_BASE_PATH

    def clone_release(self, git_url: str, tag: str, deployment_id: str) -> str:
        """
        Klone nur einen bestimmten Release/Tag als Shallow-Clone.
        Args:
            git_url: URL des Git-Repos
            tag: Tag/Release-Name
            deployment_id: Eindeutige ID für den Zielordner
        Returns:
            Pfad zum geklonten Repo
        Raises:
            Exception with detailed error message
        """
        repo_path = os.path.join(self.base_path, f"deploy_{deployment_id}")
        if os.path.exists(repo_path):
            logger.info(f"Removing existing repo at {repo_path}")
            shutil.rmtree(repo_path)
        try:
            # --branch <tag> --depth 1 klont nur den Tag, keine Historie!
            logger.info(f"Cloning repository {git_url}")
            logger.info(f"Target: {repo_path}")
            logger.info(f"Branch/Tag: {tag}")
            logger.info(f"Mode: Shallow clone (depth=1, single branch)")
            
            repo = git.Repo.clone_from(
                git_url,
                repo_path,
                branch=tag,
                depth=1,
                single_branch=True
            )
            
            logger.info(f"✓ Repository cloned successfully to {repo_path}")
            logger.info(f"Current HEAD: {repo.head.commit.hexsha}")
            
            return repo_path
        except git.exc.GitCommandError as e:
            error_msg = f"Git command failed: {e.stderr if hasattr(e, 'stderr') else str(e)}"
            logger.error(error_msg)
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Failed to clone release {tag} from {git_url}: {str(e)}"
            logger.error(error_msg)
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
            raise Exception(error_msg)

    def cleanup_repository(self, repo_path: str) -> None:
        """Löscht das geklonte Repo"""
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)

# Singleton
git_service = GitService()