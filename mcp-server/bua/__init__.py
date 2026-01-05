# -*- coding: utf-8 -*-
"""
BUA (Browser Use Agent) Module
LLM 기반 범용 브라우저 자동화 에이전트

참고: Browser Use Agent 개발 여정 (김기훈, Samsung Research)
"""

from .snapshot import SnapshotExtractor, PageSnapshot, ElementInfo, snapshot_to_text
from .tools import BrowserTools, Action, ActionResult, ActionType, parse_action_from_dict
from .agent import (
    BrowserUseAgent, 
    AgentConfig, 
    StepRecord, 
    PageSummary,
    anthropic_llm_callback,
    openai_llm_callback
)

__all__ = [
    # Snapshot
    'SnapshotExtractor',
    'PageSnapshot', 
    'ElementInfo',
    'snapshot_to_text',
    
    # Tools
    'BrowserTools',
    'Action',
    'ActionResult', 
    'ActionType',
    'parse_action_from_dict',
    
    # Agent
    'BrowserUseAgent',
    'AgentConfig',
    'StepRecord',
    'PageSummary',
    'anthropic_llm_callback',
    'openai_llm_callback'
]