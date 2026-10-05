"""Focused commit planning extension; reuse the Phase 8A isolated transport."""
import planner_provider as base

MODEL = base.MODEL
VERSION = 'phase8a-commit-v2'
MAX_STEPS = base.MAX_STEPS
ALLOWED = (*base.ALLOWED, 'commit')
POLICY = base.POLICY.replace('phase8a-v1','phase8a-commit-v2').replace(
    'unsupported or mutating,','unsupported or mutating outside the supplied commit capability,') + '''
Commit is one local commit of all changes using a deterministic default message.
Custom commit messages, Git flags, staging paths, amend, push, fetch, pull, branch
changes and arbitrary Git commands are unsupported. Reject the WHOLE goal if it
contains one, even alongside valid build/test/commit steps. Never return a prefix.
'''
PROMPT = base.PROMPT
Reply = base.Reply
shared = base.shared
workspace_path = base.workspace_path
project_metadata = base.project_metadata
SETTINGS = dict(allowed=ALLOWED,version=VERSION,policy=POLICY)


def output_schema(projects):return base.output_schema(projects,ALLOWED)
def contents(projects):return base.contents(projects,**SETTINGS)
def propose(goal,projects):return base.propose(goal,projects,settings=SETTINGS)
