import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    github_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    github_access_token = Column(String(255), nullable=True)
    
    # Subscriptions / Stripe
    stripe_customer_id = Column(String(100), unique=True, nullable=True)
    subscription_tier = Column(String(50), default="free")  # free, pro
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    repositories = relationship("Repository", back_populates="owner")

class Repository(Base):
    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    full_name = Column(String(255), unique=True, nullable=False)  # e.g., "user/repo"
    github_id = Column(Integer, unique=True, nullable=True)
    github_installation_id = Column(Integer, nullable=True)
    
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    owner = relationship("User", back_populates="repositories")
    
    status = Column(String(50), default="pending")  # pending, indexing, synced, failed
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    files = relationship("RepositoryFile", back_populates="repository", cascade="all, delete-orphan")
    chunks = relationship("CodeChunk", back_populates="repository", cascade="all, delete-orphan")

class RepositoryFile(Base):
    __tablename__ = "repository_files"

    id = Column(Integer, primary_key=True, index=True)
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    repository = relationship("Repository", back_populates="files")
    
    filepath = Column(String(1024), nullable=False)  # path relative to repo root
    sha = Column(String(100), nullable=True)
    size = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    chunks = relationship("CodeChunk", back_populates="file", cascade="all, delete-orphan")

class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id = Column(Integer, primary_key=True, index=True)
    
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    repository = relationship("Repository", back_populates="chunks")
    
    file_id = Column(Integer, ForeignKey("repository_files.id", ondelete="CASCADE"), nullable=False)
    file = relationship("RepositoryFile", back_populates="chunks")
    
    name = Column(String(255), nullable=False)  # function/class name or "module"
    type = Column(String(50), nullable=False)  # "class", "function", "module"
    
    start_line = Column(Integer, nullable=False)
    end_line = Column(Integer, nullable=False)
    
    code_content = Column(Text, nullable=False)
    
    # pgvector embedding: 1536 dimensions for OpenAI text-embedding-3-small
    embedding = Column(Vector(1536), nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
