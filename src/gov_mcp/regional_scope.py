"""Region profile of a frozen-corpus service: which sources and which corpora it may serve.

One region, one agent: the Henan services may only load an approved corpus whose every unit
belongs to an approved Henan or explicitly listed national source. A corpus built or copied
by mistake with another region's units (the region-isolation audit's case M08) is refused at
startup, and a displayed answer citing a source outside the profile is refused at the end, so
nothing is silently dropped and the service never runs on a mixed corpus.

The profile is a side-car file; the corpus bytes and hashes are unchanged. Nothing here reads
the region from the user's question.
"""
import json
from pathlib import Path

DEFAULT_PROFILE = Path(__file__).resolve().parents[2] / "config" / "region-profiles" / "henan-frozen.json"


class RegionScopeError(ValueError):
    pass


def load_profile(path=DEFAULT_PROFILE):
    profile = json.loads(Path(path).read_text("utf-8"))
    if not profile.get("sources") or not profile.get("approved_corpus_unit_sha256"):
        raise RegionScopeError("REGION_PROFILE_INCOMPLETE")
    if set(profile["sources"].values()) - {"henan", "national"}:
        raise RegionScopeError("REGION_PROFILE_SOURCE_ROLE_UNKNOWN")
    return profile


def check_corpus(units, corpus_unit_sha256, profile, *, require_approved_hash=True):
    """Raise unless every unit's source is in the profile and, if required, the corpus is approved."""
    unknown = sorted({unit["source_id"] for unit in units} - set(profile["sources"]))
    if unknown:
        raise RegionScopeError("REGION_PROFILE_SOURCE_NOT_APPROVED: " + ", ".join(unknown[:5]))
    if require_approved_hash and corpus_unit_sha256 not in profile["approved_corpus_unit_sha256"]:
        raise RegionScopeError("REGION_PROFILE_CORPUS_NOT_APPROVED: " + str(corpus_unit_sha256)[:12])
    return {"profile": profile["profile"], "sources": len(profile["sources"]), "units": len(units)}


def unapproved_citations(final, profile):
    """source_ids cited by a displayed answer that the profile does not approve."""
    return sorted({item["source_id"] for item in final.get("assertions", [])} - set(profile["sources"]))
