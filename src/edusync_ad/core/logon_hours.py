"""Heures de connexion / ``logonHours`` (M16).

Grille 24h × 7j (lundi → dimanche côté affichage), application en masse
(OU / groupe) ou sur un compte unique, avec préréglages « Heures cours »,
« Heures admin », « Personnalisé ».

Encodage AD (MS-SAMR) :
- 21 octets = 168 bits (7 jours × 24 h), 1 bit par heure (1 = connexion autorisée)
- Semaine stockée **dimanche en premier** (jour 0 = dimanche … jour 6 = samedi)
- Dans chaque octet, le bit de poids faible = la 1re heure du bloc de 8 h
- L'attribut est stocké en **UTC** : ADUC convertit vers l'heure locale à
  l'affichage. On applique donc une rotation hebdomadaire de ``utc_offset_hours``
  à l'écriture (heure locale → UTC) et à la lecture (UTC → locale).
- Attribut absent = toutes les heures autorisées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

# -- Constantes ----------------------------------------------------------------

DAYS = 7
HOURS_PER_DAY = 24
TOTAL_BITS = DAYS * HOURS_PER_DAY   # 168
TOTAL_BYTES = TOTAL_BITS // 8       # 21

# Ordre d'affichage (français) : index 0 = lundi … index 6 = dimanche
DISPLAY_DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
# Ordre de stockage AD : index 0 = dimanche … index 6 = samedi
STORAGE_DAYS = ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"]

Grid = list[list[bool]]  # [jour affiché 0..6][heure 0..23]


# -- Grille ----------------------------------------------------------------------

def new_grid(value: bool = False) -> Grid:
    """Grille 7×24 en ordre d'affichage (lundi d'abord)."""
    return [[bool(value) for _ in range(HOURS_PER_DAY)] for _ in range(DAYS)]


def display_to_storage_index(day: int) -> int:
    """Index jour en ordre affichage (0=lundi) → ordre stockage (0=dimanche)."""
    return (day + 1) % DAYS


def is_unrestricted(grid: Grid) -> bool:
    return all(grid[d][h] for d in range(DAYS) for h in range(HOURS_PER_DAY))


def is_empty(grid: Grid) -> bool:
    return not any(grid[d][h] for d in range(DAYS) for h in range(HOURS_PER_DAY))


def copy_grid(grid: Grid) -> Grid:
    return [row[:] for row in grid]


# -- Encodage / décodage -----------------------------------------------------------

def current_utc_offset_hours() -> int:
    """Décalage UTC local actuel (heure d'hiver/été incluse), arrondi à l'heure."""
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return 0
    return round(offset.total_seconds() / 3600)


def _pack(bits: Sequence[bool]) -> bytes:
    data = bytearray(TOTAL_BYTES)
    for i, on in enumerate(bits):
        if on:
            data[i // 8] |= 1 << (i % 8)
    return bytes(data)


def _unpack(data: bytes) -> list[bool]:
    bits = [False] * TOTAL_BITS
    for i in range(min(len(data), TOTAL_BYTES)):
        byte = data[i]
        for b in range(8):
            if byte & (1 << b):
                bits[i * 8 + b] = True
    return bits


def encode_logon_hours(grid: Grid, *, utc_offset_hours: int = 0) -> bytes:
    """Grille locale (affichage FR) → 21 octets AD (UTC, dimanche-first)."""
    # Bits locaux dans l'ordre chronologique de la semaine (dimanche 00:00 local = 0)
    local = [False] * TOTAL_BITS
    for d in range(DAYS):
        storage_day = display_to_storage_index(d)
        for h in range(HOURS_PER_DAY):
            if grid[d][h]:
                local[storage_day * HOURS_PER_DAY + h] = True
    # Locale → UTC : utc[j] = local[(j + offset) % 168]
    utc = [local[(j + utc_offset_hours) % TOTAL_BITS] for j in range(TOTAL_BITS)]
    return _pack(utc)


def decode_logon_hours(
    data: bytes | None, *, utc_offset_hours: int = 0
) -> Grid:
    """21 octets AD (UTC) → grille locale. ``None`` = toutes les heures."""
    if not data:
        return new_grid(True)
    utc = _unpack(data)
    # UTC → locale : local[i] = utc[(i - offset) % 168]
    local = [utc[(i - utc_offset_hours) % TOTAL_BITS] for i in range(TOTAL_BITS)]
    grid = new_grid(False)
    for d in range(DAYS):
        storage_day = display_to_storage_index(d)
        for h in range(HOURS_PER_DAY):
            grid[d][h] = local[storage_day * HOURS_PER_DAY + h]
    return grid


# -- Résumé ------------------------------------------------------------------------

def _hour_label(hour: int) -> str:
    return f"{hour:02d}:00"


def _ranges_of(hours: Sequence[int]) -> str:
    """Heures autorisées → plages « 08:00-12:00, 13:00-18:00 »."""
    if not hours:
        return "fermé"
    ranges: list[str] = []
    start = prev = hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        ranges.append(f"{_hour_label(start)}-{_hour_label(prev + 1)}")
        start = prev = h
    ranges.append(f"{_hour_label(start)}-{_hour_label(prev + 1)}")
    return ", ".join(ranges)


def grid_summary(grid: Grid) -> str:
    """Résumé compact : « Lundi–Vendredi 08:00-18:00 ; Samedi 08:00-12:00 »."""
    if is_unrestricted(grid):
        return "Toutes heures (sans restriction)"
    if is_empty(grid):
        return "Aucune heure autorisée"

    groups: list[tuple[list[int], tuple[int, ...]]] = []
    for d in range(DAYS):
        profile = tuple(h for h in range(HOURS_PER_DAY) if grid[d][h])
        if groups and groups[-1][1] == profile and groups[-1][0][-1] == d - 1:
            groups[-1][0].append(d)
        else:
            groups.append(([d], profile))

    parts: list[str] = []
    for days, profile in groups:
        label = (
            DISPLAY_DAYS[days[0]]
            if len(days) == 1
            else f"{DISPLAY_DAYS[days[0]]}–{DISPLAY_DAYS[days[-1]]}"
        )
        if not profile:
            parts.append(f"{label} fermé")
        else:
            parts.append(f"{label} {_ranges_of(profile)}")
    return " ; ".join(parts)


# -- Préréglages ----------------------------------------------------------------------

@dataclass(frozen=True)
class LogonHoursPreset:
    """Préréglage : plages (jours affichés, heure début, heure fin exclusive)."""

    name: str
    description: str
    ranges: tuple = field(default_factory=tuple)

    def build(self) -> Grid:
        grid = new_grid(False)
        for days, start, end in self.ranges:
            for d in days:
                for h in range(max(start, 0), min(end, HOURS_PER_DAY)):
                    grid[d][h] = True
        return grid

    @classmethod
    def builtin(cls) -> list["LogonHoursPreset"]:
        weekdays = (0, 1, 2, 3, 4)  # lundi → vendredi (ordre affichage)
        return [
            cls(
                name="Heures cours",
                description="Lun–Ven 8h–18h, Sam 8h–12h (rythme scolaire)",
                ranges=((weekdays, 8, 18), ((5,), 8, 12)),
            ),
            cls(
                name="Heures admin",
                description="Lun–Ven 7h–20h, Sam 9h–13h (personnel encadrant)",
                ranges=((weekdays, 7, 20), ((5,), 9, 13)),
            ),
            cls(
                name="Personnalisé",
                description="Conserver la grille éditée manuellement (aucun modèle)",
                ranges=(),
            ),
        ]


PRESET_COURS = "Heures cours"
PRESET_ADMIN = "Heures admin"
PRESET_CUSTOM = "Personnalisé"


# -- Gestionnaire ---------------------------------------------------------------------

class LogonHoursManager:
    """Lit / écrit ``logonHours`` en tenant compte du décalage UTC local."""

    def __init__(self, connection, *, utc_offset_hours: int | None = None) -> None:
        self._ad = connection
        self.utc_offset_hours = (
            current_utc_offset_hours() if utc_offset_hours is None else utc_offset_hours
        )

    def get_grid(self, user_dn: str) -> Grid:
        """Grille locale d'un compte (attribut absent = toutes les heures)."""
        data = self._ad.get_logon_hours(user_dn)
        return decode_logon_hours(data, utc_offset_hours=self.utc_offset_hours)

    def apply(self, user_dn: str, grid: Grid) -> str:
        """Applique la grille à un compte.

        Retourne « clear » (sans restriction, attribut supprimé), « set » ou
        « none » (aucune modification nécessaire).
        """
        if is_unrestricted(grid):
            current = self._ad.get_logon_hours(user_dn)
            if current is None:
                return "none"
            self._ad.clear_logon_hours(user_dn)
            return "clear"
        payload = encode_logon_hours(grid, utc_offset_hours=self.utc_offset_hours)
        if self._ad.get_logon_hours(user_dn) == payload:
            return "none"
        self._ad.set_logon_hours(user_dn, payload)
        return "set"
