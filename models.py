"""
Minimal database models for worker operations
IMPORTANT: These models MUST match the backend models structure!
Only includes models and fields needed for deployment tasks.
Full models are in: backend/app/models.py

If you change the backend models, update these accordingly!
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Enum, LargeBinary
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from database import Base
import enum
import uuid

# ----------------------------------------------------------------
# ENUMS (must match backend/app/models.py)
# ----------------------------------------------------------------
class DeploymentStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

# ----------------------------------------------------------------
# DEPLOYMENT MODEL (minimal version for worker)
# ----------------------------------------------------------------
class Deployment(Base):
    __tablename__ = "deployments"
    
    deploymentId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    status = Column(Enum(DeploymentStatus), default=DeploymentStatus.PENDING)
    commitHash = Column(String, nullable=True)
    commitInfo = Column(Text, nullable=True)
    userInputVar = Column(Text, nullable=True)
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False)
    appId = Column(UUID(as_uuid=True), ForeignKey("apps.appId"), nullable=False)

# ----------------------------------------------------------------
# APP MODEL (minimal version for worker)
# ----------------------------------------------------------------
class App(Base):
    __tablename__ = "apps"
    
    appId = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    image = Column(LargeBinary, nullable=True)
    git_link = Column(String, nullable=True)
    userId = Column(UUID(as_uuid=True), ForeignKey("users.userId"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
