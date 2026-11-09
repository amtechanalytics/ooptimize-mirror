"""OOPTIMIZE: do LLM performance rewrites of hot Java library methods break object-oriented contracts that the
project's own tests do not catch, and does more surrounding code (method -> class -> class + hierarchy + call
sites) change that? Stages (every rung runs all of them; pilot on one library, canary and full on five):
  toolchain -> build -> select -> baseline -> generate (LLM) -> score -> alloc."""
from __future__ import annotations

from rh.context import Stage

from . import fakes
from .analysis import analyze, brief
from .s_alloc import check_alloc, run_alloc
from .s_baseline import check_baseline, run_baseline
from .s_build import check_build, check_toolchain, run_build, run_toolchain
from .s_generate import build_generate, check_generate, schema
from .s_score import check_score, run_score
from .s_select import check_select, run_select

STAGES = [
    Stage("toolchain", "local", run=run_toolchain, gate={"check": check_toolchain}),
    Stage("build", "local", run=run_build, gate={"check": check_build}),
    Stage("select", "local", run=run_select, gate={"check": check_select}),
    Stage("baseline", "local", run=run_baseline, gate={"check": check_baseline}),
    Stage("generate", "llm", build=build_generate, schema=schema,
          gate={"min_parse_rate": 0.85, "max_duplicate_cells": 0, "check": check_generate}),
    Stage("score", "local", run=run_score, gate={"check": check_score}),
    Stage("alloc", "local", run=run_alloc, gate={"check": check_alloc}),
]


def fake_response(model_id: str, prefix: str, item: str) -> str:
    return fakes.response(model_id, prefix, item)


__all__ = ["STAGES", "analyze", "brief", "fake_response"]
