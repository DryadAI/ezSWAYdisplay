"""Imports saved layouts from other display-profile tools into
ezSWAYdisplay's profile format.

Two dialects are supported: this machine's pre-ezSWAYdisplay ad-hoc
`~/.config/sway/config.d/.locations/*.conf` format (block-style
`output "<descriptor>" { mode ... \n position X,Y \n ... }`, plus a
single-line `output <connector> mode ... position ...` variant used for
per-connector blanket rules), and kanshi's native config format (named
`profile <name> { output <criteria> ... }` blocks, one line per output).

Neither format carries live EDID data -- a descriptor ("eDP-1", or a quoted
"Make Model Serial" string) is only resolvable to a unique_id by matching it
against currently-connected monitors. Anything that doesn't match a live
monitor is reported as unresolved rather than guessed at.
"""
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .wm_adapter import Monitor

_BLOCK_OPEN_RE = re.compile(r'^output\s+("(?:[^"]+)"|\S+)\s*\{\s*$')
_SINGLELINE_RE = re.compile(r'^output\s+("(?:[^"]+)"|\S+)\s+(\S.*)$')
_PROFILE_OPEN_RE = re.compile(r'^profile\s*(\S*)\s*\{\s*$')

_ATTR_MODE_RE = re.compile(r'\bmode\s+(\d+x\d+(?:@[\d.]+(?:Hz)?)?)', re.IGNORECASE)
_ATTR_POSITION_RE = re.compile(r'\bposition\s+(-?\d+)\s*,\s*(-?\d+)')
_ATTR_SCALE_RE = re.compile(r'\bscale\s+([\d.]+)')
_ATTR_TRANSFORM_RE = re.compile(r'\btransform\s+(\S+)')
_ATTR_DISABLE_RE = re.compile(r'\bdisable\b')


class ParsedOutput:
    def __init__(self, criteria: str, enabled: bool = True, mode: Optional[str] = None,
                 position: Optional[str] = None, scale: Optional[float] = None,
                 transform: Optional[str] = None):
        self.criteria = criteria
        self.enabled = enabled
        self.mode = mode
        self.position = position
        self.scale = scale
        self.transform = transform


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0]


def _parse_attrs(criteria: str, body: str) -> ParsedOutput:
    mode_m = _ATTR_MODE_RE.search(body)
    pos_m = _ATTR_POSITION_RE.search(body)
    scale_m = _ATTR_SCALE_RE.search(body)
    transform_m = _ATTR_TRANSFORM_RE.search(body)
    return ParsedOutput(
        criteria=criteria,
        enabled=not _ATTR_DISABLE_RE.search(body),
        mode=mode_m.group(1) if mode_m else None,
        position=f"{pos_m.group(1)} {pos_m.group(2)}" if pos_m else None,
        scale=float(scale_m.group(1)) if scale_m else None,
        transform=transform_m.group(1) if transform_m else None,
    )


def parse_locations_conf(path: Path) -> List[ParsedOutput]:
    """Parses one .locations-style profile file (single profile per file)."""
    lines = path.read_text().splitlines()
    outputs = []
    i = 0
    while i < len(lines):
        line = _strip_comment(lines[i]).strip()
        i += 1
        if not line:
            continue
        block_m = _BLOCK_OPEN_RE.match(line)
        if block_m:
            criteria = block_m.group(1).strip('"')
            body_lines = []
            while i < len(lines) and _strip_comment(lines[i]).strip() != "}":
                body_lines.append(lines[i])
                i += 1
            i += 1  # skip closing brace
            outputs.append(_parse_attrs(criteria, " ".join(body_lines)))
            continue
        single_m = _SINGLELINE_RE.match(line)
        if single_m:
            criteria = single_m.group(1).strip('"')
            outputs.append(_parse_attrs(criteria, single_m.group(2)))
    return outputs


def parse_kanshi_config(path: Path) -> Dict[str, List[ParsedOutput]]:
    """Parses a kanshi config into {profile_name: [ParsedOutput, ...]}.
    Anonymous `profile { ... }` blocks (valid kanshi syntax) are skipped --
    there's no label to import them under."""
    lines = path.read_text().splitlines()
    profiles: Dict[str, List[ParsedOutput]] = {}
    i = 0
    while i < len(lines):
        line = _strip_comment(lines[i]).strip()
        i += 1
        profile_m = _PROFILE_OPEN_RE.match(line)
        if not profile_m:
            continue
        name = profile_m.group(1)
        body_lines = []
        while i < len(lines) and _strip_comment(lines[i]).strip() != "}":
            body_lines.append(lines[i])
            i += 1
        i += 1  # skip closing brace
        if not name:
            continue
        outputs = []
        for raw in body_lines:
            bl = _strip_comment(raw).strip()
            if not bl:
                continue
            single_m = _SINGLELINE_RE.match(bl)
            if single_m:
                criteria = single_m.group(1).strip('"')
                outputs.append(_parse_attrs(criteria, single_m.group(2)))
        profiles[name] = outputs
    return profiles


def _index_live_monitors(monitors: List[Monitor]):
    by_name = {m.name: m for m in monitors}
    by_descriptor = {f"{m.make} {m.model} {m.serial}": m for m in monitors}
    return by_name, by_descriptor


def resolve_to_profile_outputs(parsed: List[ParsedOutput],
                                live_monitors: List[Monitor]) -> Tuple[List[dict], List[str]]:
    """Resolves parsed criteria against currently-connected monitors,
    producing entries in the same shape ProfileManager.save_profile writes.
    Returns (outputs, unresolved_criteria) -- unresolved entries reference
    hardware that isn't plugged in right now, so no unique_id can be
    determined for them; they're dropped, not guessed at."""
    by_name, by_descriptor = _index_live_monitors(live_monitors)
    outputs = []
    unresolved = []
    for p in parsed:
        live = by_name.get(p.criteria) or by_descriptor.get(p.criteria)
        if live is None:
            unresolved.append(p.criteria)
            continue
        mode = p.mode or f"{int(live.width)}x{int(live.height)}@{live.refresh_rate:.3f}Hz"
        outputs.append({
            "unique_id": live.unique_id,
            "name": live.name,
            "mode": mode,
            "position": p.position if p.position is not None else "0 0",
            "scale": p.scale if p.scale is not None else 1.0,
            "transform": p.transform if p.transform is not None else "normal",
            "active": p.enabled,
        })
    return outputs, unresolved
