"""CDV: the judge for AI coding agents — verified refinement loops and audit trails."""
from __future__ import annotations

from cdv.adaptive_exit import BayesianExitCondition
from cdv.adapters import AdaptiveStopper
from cdv.agent_loop import AgentLoopController, AgentLoopSession
from cdv.elicitation import (
    ClarifyingQuestion,
    ElicitationSession,
    IntentRefiner,
    IntentSpec,
)
from cdv.engine import (
    CompositeEvaluator,
    Evaluator,
    EvaluationResult,
    ExitConditionProtocol,
    ExitReason,
    IterationRecord,
    LoopConfig,
    LoopedLLM,
    LoopMetrics,
    RefinementResult,
)
from cdv.episodes import EpisodicStore
from cdv.guards import AgentLoopGuard, GuardContext, GuardStack
from cdv.priors import AdaptivePriors, CallObservation
from cdv.step_scorer import DualVerifyScore, conservative_dual_verify
from cdv.store import LoopStore, SQLiteBackedPriors
from cdv.tasks import Task, TaskOrchestrator, TaskPlan, TaskState

__version__ = "1.0.1"

__all__ = [
    # Engine
    "LoopedLLM",
    "LoopConfig",
    "EvaluationResult",
    "ExitReason",
    "IterationRecord",
    "LoopMetrics",
    "RefinementResult",
    "CompositeEvaluator",
    "Evaluator",
    "ExitConditionProtocol",
    # Priors
    "AdaptivePriors",
    "CallObservation",
    "BayesianExitCondition",
    # Agent loops
    "AgentLoopController",
    "AgentLoopSession",
    "AgentLoopGuard",
    "GuardContext",
    "GuardStack",
    "AdaptiveStopper",
    "DualVerifyScore",
    "conservative_dual_verify",
    # Elicitation
    "IntentRefiner",
    "IntentSpec",
    "ClarifyingQuestion",
    "ElicitationSession",
    # Store
    "LoopStore",
    "SQLiteBackedPriors",
    # Tasks
    "Task",
    "TaskPlan",
    "TaskState",
    "TaskOrchestrator",
    # Episodic memory
    "EpisodicStore",
]
