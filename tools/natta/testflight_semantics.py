"""Conservative local veto for unsupported public-distribution requests.

This does not select a capability, extract parameters or execute anything. It
prevents a provider from reducing an explicitly public-release goal to a prefix.
"""
import re


def public_release_requested(request):
    return bool(re.search(r'\b(?:publish|distribute|distribution workflow)\b|'
        r'\bsubmit\b.{0,80}\b(?:review|app store)\b|'
        r'\brelease\b.{0,60}\b(?:publicly|public|app store)\b|'
        r'\b(?:phased|automatic|manual) release\b', request, re.I))
