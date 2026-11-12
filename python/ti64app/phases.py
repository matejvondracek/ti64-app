"""Phase identification of .ang maps by crystal symmetry, never by name (phase 6.6 fix 4).

APEX exports name the phases "Ti (Titanium, alpha)" / "Ti (Titanium, beta)", older OIM exports "Ti (alpha)" /
"Ti (beta)"; other exports may use anything. The app works with two canonical names, ALPHA and BETA, which every
backend sets as the phase (mineral) name when it loads a map, so all later steps find the phases the same way:

  Laue group 6/mmm (TSL symmetry code 62)  -> ALPHA  (hcp alpha-Ti)
  Laue group m-3m  (TSL symmetry code 43)  -> BETA   (bcc beta-Ti)
  anything else                            -> kept under its own name ("other")

The original names stay in the load metadata. When the symmetry does not decide (two hexagonal phases, two cubic
phases), the user sets the mapping in the map sheet (Files tab, section 'Phases'); loading stops with a message
naming that place until it is set. The phase NUMBERS of the file are kept (1, 2, ... as in the header; a single-phase
file, whose rows say 0, is phase 1 in both backends).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from .sample import SheetIncomplete

ALPHA, BETA = "Ti (alpha)", "Ti (beta)"
Role = Literal["alpha", "beta", "other"]
CANONICAL: dict[str, str] = {"alpha": ALPHA, "beta": BETA}
ROLE_LABEL = {"alpha": "α (Ti alpha)", "beta": "β (Ti beta)", "other": "other (keep its name)"}

# TSL/OIM '# Symmetry' codes -> Laue group (OIM manual, 'Symmetry' in the .ang header)
TSL_LAUE = {"1": "-1", "2": "2/m", "20": "2/m", "21": "2/m", "22": "mmm", "4": "4/m", "42": "4/mmm", "3": "-3",
            "32": "-3m", "6": "6/m", "62": "6/mmm", "23": "m-3", "43": "m-3m"}
ROLE_OF_LAUE = {"6/mmm": "alpha", "m-3m": "beta"}


class PhaseMappingAmbiguous(SheetIncomplete):
    """The crystal symmetries do not tell which phase is alpha / beta; the user decides in the map sheet (the GUI shows
    it like any missing map-sheet value, with an 'Open map sheet' button)."""

    def __init__(self, map_id: str, detail: str) -> None:
        self.map_id, self.missing, self.needed_for = map_id, "phase mapping", "Loading"
        ValueError.__init__(self, f"The phases of map {map_id!r} cannot be told apart by their crystal symmetry ({detail}). "
                         "Choose which phase is α and which is β on the Files tab: select the file, section 'Phases' "
                         "of the map sheet.")


@dataclass
class PhaseInfo:
    id: int                      # phase number in the file header (1, 2, ...)
    original: str                # MaterialName in the file
    laue: str                    # Laue group from the symmetry code, '' if unknown
    suggested: Role              # from the symmetry
    role: Role                   # used (suggested, or the user's choice in the map sheet)
    source: Literal["symmetry", "map sheet"]
    name: str = ""               # name the backends give the phase (ALPHA / BETA / original)

    def as_dict(self) -> dict:
        return asdict(self)


def laue_of(symmetry_code: str | int | None) -> str:
    return TSL_LAUE.get(str(symmetry_code).strip(), "") if symmetry_code is not None else ""


def resolve(header_phases: list[dict], overrides: dict[str, str] | None = None) -> tuple[list[PhaseInfo], list[str]]:
    """Phases of an .ang header (read_header().phases: id, MaterialName, Symmetry) -> (PhaseInfo per phase, problems).
    `overrides` = the map sheet's phase mapping {str(phase id): 'alpha' | 'beta' | 'other'}. Problems are plain-language
    reasons why the mapping is ambiguous (empty = usable)."""
    overrides = {str(k): v for k, v in (overrides or {}).items()}
    out: list[PhaseInfo] = []
    for ph in sorted(header_phases, key=lambda p: int(p.get("id", 0))):
        laue = laue_of(ph.get("Symmetry"))
        sug: Role = ROLE_OF_LAUE.get(laue, "other")
        own = overrides.get(str(ph.get("id")))
        role: Role = own if own in ("alpha", "beta", "other") else sug
        out.append(PhaseInfo(int(ph.get("id", 0)), str(ph.get("MaterialName", "")).strip() or f"phase {ph.get('id')}",
                             laue, sug, role, "map sheet" if own in ("alpha", "beta", "other") else "symmetry"))
    problems = []
    for r in ("alpha", "beta"):
        hits = [p for p in out if p.role == r]
        if len(hits) > 1:
            problems.append(f"{len(hits)} phases would be {'α' if r == 'alpha' else 'β'}: "
                            + ", ".join(f"{p.id} '{p.original}' ({p.laue or 'unknown symmetry'})" for p in hits))
    canon = set(CANONICAL.values())
    for p in out:
        p.name = CANONICAL[p.role] if p.role in CANONICAL else (p.original if p.original not in canon
                                                                  else f"{p.original} (other)")
    return out, problems


def load_names(phases: list[PhaseInfo]) -> dict[int, str]:
    """{file phase id: name the backend sets} for LoadParams.phase_names."""
    return {p.id: p.name for p in phases}


def for_file(path, overrides: dict[str, str] | None = None, map_id: str = "") -> list[PhaseInfo]:
    """Resolved phases of an .ang file; raises PhaseMappingAmbiguous when the user has to decide."""
    from .io.ang import read_header  # noqa: PLC0415

    phases, problems = resolve(read_header(path).phases, overrides)
    if problems:
        raise PhaseMappingAmbiguous(map_id or str(path), "; ".join(problems))
    return phases


def phase_ids(phase_names: dict) -> dict[str, int]:
    """{'alpha': id, 'beta': id} from a map's {id: name} (load metadata); absent phases are missing."""
    out = {}
    for k, v in phase_names.items():
        for r, n in CANONICAL.items():
            if v == n:
                out[r] = int(k)
    return out
