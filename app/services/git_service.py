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
        """
        repo_path = os.path.join(self.base_path, f"deploy_{deployment_id}")
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)
        try:
            # --branch <tag> --depth 1 klont nur den Tag, keine Historie!
            logger.info(f"Cloning only tag {tag} from {git_url} to {repo_path}")
            repo = git.Repo.clone_from(
                git_url,
                repo_path,
                branch=tag,
                depth=1,
                single_branch=True
            )
            return repo_path
        except Exception as e:
            logger.error(f"Failed to clone release {tag}: {e}")
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
            raise

    def cleanup_repository(self, repo_path: str) -> None:
        """Löscht das geklonte Repo"""
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)

# Singleton
git_service = GitService()