"""Generic batch harness for WorkBuddy/CodeBuddy headless agents."""

from .models import BatchConfig, CaseSpec, Interaction
from .runner import BatchRunner

__all__ = ["BatchConfig", "BatchRunner", "CaseSpec", "Interaction"]

__version__ = "0.1.0"
