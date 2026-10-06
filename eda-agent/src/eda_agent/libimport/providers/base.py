# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Provider-neutral contract for part sources.

Every source of parts (a public registry, a vendor API, a local library)
implements :class:`PartProvider`. Nothing here ranks providers, and
nothing designates a default: :func:`search_all` queries all of them and
returns their hits attributed to their source, so the caller decides.

That is a deliberate structural choice, not a convention to remember. A
"preferred" provider would quietly become the answer to every query, and
the operator of that provider would inherit the whole tool surface. Any
ordering applied to merged results is alphabetical by provider then by
part, which carries no quality judgement.

A provider that is unavailable (endpoint withdrawn, needs credentials,
software not installed) must say so through
:class:`ProviderUnavailable` rather than returning nothing. Silence
reads as "no parts matched", which is a different and misleading answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "PartHit",
    "PartProvider",
    "ProviderError",
    "ProviderUnavailable",
]


#: Formats this server can actually convert into an Altium library, and
#: the tool that does it. A provider may only claim ``usable_in``
#: "altium" if one of its formats appears here: the claim has to be tied
#: to a converter that exists, not to an intention.
ALTIUM_CONVERTIBLE_FORMATS = {
    "easyeda_json": "lib_easyeda_import",
    "kicad_sym": "lib_kicad_import",
    "kicad_mod": "lib_kicad_import",
}


class ProviderError(RuntimeError):
    """A provider failed in a way the caller may want to see."""


class ProviderUnavailable(ProviderError):
    """The provider cannot answer at all right now.

    Distinct from "no results": a withdrawn endpoint, a missing
    credential or an uninstalled tool is not evidence that the part does
    not exist, and must never be reported as an empty result set.
    """


@dataclass
class PartHit:
    """One candidate part, attributed to the provider that found it."""

    provider: str
    part_id: str
    mpn: str = ""
    manufacturer: str = ""
    package: str = ""
    description: str = ""
    datasheet: str = ""
    #: What the provider says about where its geometry came from. Free
    #: text; ``design_validate``'s atomic-parts checks want provenance,
    #: and a source that cannot supply any is itself a signal.
    provenance: str = ""
    #: License of the downloadable artefacts, when the provider states
    #: one. Blank means unknown, which is not the same as permissive.
    license: str = ""
    #: Anything provider-specific a later fetch needs.
    extra: dict[str, Any] = field(default_factory=dict)

    def sort_key(self) -> tuple[str, str, str]:
        """Neutral ordering: provider, then MPN, then id.

        Explicitly NOT a relevance or quality ranking. Merged results
        must not imply one source is better than another.
        """
        return (self.provider.lower(), (self.mpn or "").lower(),
                self.part_id.lower())

    @property
    def ref(self) -> str:
        """One handle that carries its own source: ``provider:part_id``.

        Lets ``part_fetch`` take a single argument instead of making the
        caller pair an id with the provider it came from. The source is
        still explicit, it just travels WITH the id rather than beside
        it, so merged results behave like one catalogue without any
        source becoming an implied default.

        Split on the FIRST colon only: provider names are identifiers
        and contain none, while part ids routinely do (``Device:R``,
        ``Lib.SchLib::Comp``).
        """
        return f"{self.provider}:{self.part_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "provider": self.provider,
            "part_id": self.part_id,
            "mpn": self.mpn,
            "manufacturer": self.manufacturer,
            "package": self.package,
            "description": self.description,
            "datasheet": self.datasheet,
            "provenance": self.provenance,
            "license": self.license,
        }


@runtime_checkable
class PartProvider(Protocol):
    """What a part source must implement to take part in a search."""

    #: Stable lowercase identifier, used to address the provider.
    name: str

    #: One line for the catalog, including any disclosure the operator
    #: should see (who runs it, what it costs, whether it needs a login).
    description: str

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        """Candidates matching ``query``.

        Raise :class:`ProviderUnavailable` when the source cannot be
        reached or has no search facility. Return an empty list ONLY
        when the search genuinely ran and matched nothing.
        """
        ...

    def fetch(self, part_id: str) -> dict[str, Any]:
        """Everything needed to build the part, provider-specific shape.

        Raise :class:`ProviderError` on failure.
        """
        ...
