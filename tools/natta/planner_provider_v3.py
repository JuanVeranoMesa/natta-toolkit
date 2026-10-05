"""Additive TestFlight planner controls; preserve historical v1/v2 inputs."""
import planner_provider_v2 as previous

base = previous.base
MODEL = previous.MODEL
VERSION = 'testflight-planner-v1'
MAX_STEPS = previous.MAX_STEPS
ALLOWED = (*previous.ALLOWED, 'testflight')
POLICY = previous.POLICY + '''
TestFlight means a signed Release archive and upload to App Store Connect's beta
build pipeline only. It never submits App Review, publishes publicly, adds testers,
changes release settings or bumps a build number. Public App Store release,
publish, distribute and upload-plus-publication goals are unsupported: reject the
WHOLE goal with no_match, never a TestFlight-only prefix. Upload/beta-build delivery
phrasing may select testflight; build then upload is build/testflight. Explicit
build/test/commit/upload is four steps. Keep max4 and exact project/argument rules.
'''
PROMPT = previous.PROMPT
Reply = previous.Reply
shared = previous.shared
workspace_path = previous.workspace_path
project_metadata = previous.project_metadata
SETTINGS = dict(allowed=ALLOWED, version=VERSION, policy=POLICY)


def output_schema(projects): return base.output_schema(projects, ALLOWED)
def contents(projects): return base.contents(projects, **SETTINGS)
def propose(goal, projects): return base.propose(goal, projects, settings=SETTINGS)
