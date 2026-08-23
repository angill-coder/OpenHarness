"""OpenHarness 内置的 WorkBuddy 批量执行实现。

外部模块不要直接依赖这里的内部细节；统一通过
``harness/workbuddy_runner.py`` 的 façade 调用。
"""

from .models import BatchConfig, CaseSpec, Interaction
from .runner import BatchRunner

__all__ = ["BatchConfig", "BatchRunner", "CaseSpec", "Interaction"]

__version__ = "0.1.0-openharness"
