"""Tests du module Heures de connexion / logonHours (M16)."""

from __future__ import annotations

from edusync_ad.core.logon_hours import (
    DAYS,
    DISPLAY_DAYS,
    HOURS_PER_DAY,
    TOTAL_BITS,
    TOTAL_BYTES,
    LogonHoursManager,
    LogonHoursPreset,
    current_utc_offset_hours,
    decode_logon_hours,
    display_to_storage_index,
    encode_logon_hours,
    grid_summary,
    is_empty,
    is_unrestricted,
    new_grid,
)

# Vecteur de référence (NetTools, machine en UTC) : Lun–Ven 6h–18h (heures 6..17)
REF_MON_FRI_6_18 = bytes(
    [0x00, 0x00, 0x00] + [0xC0, 0xFF, 0x03] * 5 + [0x00, 0x00, 0x00]
)


def grid_with(days_hours: dict[int, list[int]]) -> list[list[bool]]:
    grid = new_grid(False)
    for day, hours in days_hours.items():
        for h in hours:
            grid[day][h] = True
    return grid


class TestGridBasics:
    def test_grid_shape(self):
        grid = new_grid()
        assert len(grid) == DAYS
        assert all(len(row) == HOURS_PER_DAY for row in grid)

    def test_display_days_order_is_monday_first(self):
        assert DISPLAY_DAYS[0] == "Lundi"
        assert DISPLAY_DAYS[6] == "Dimanche"

    def test_display_to_storage_index(self):
        # affichage 0=lundi → stockage 1 (0=dimanche)
        assert display_to_storage_index(0) == 1
        assert display_to_storage_index(5) == 6   # samedi
        assert display_to_storage_index(6) == 0   # dimanche

    def test_is_unrestricted_and_empty(self):
        assert is_unrestricted(new_grid(True))
        assert is_empty(new_grid(False))
        assert not is_unrestricted(new_grid(False))
        assert not is_empty(new_grid(True))


class TestEncoding:
    def test_reference_vector_mon_fri(self):
        grid = grid_with({d: list(range(6, 18)) for d in range(5)})
        assert encode_logon_hours(grid) == REF_MON_FRI_6_18

    def test_reference_vector_decodes_back(self):
        grid = decode_logon_hours(REF_MON_FRI_6_18)
        for d in range(5):
            assert grid[d][6] and grid[d][17]
            assert not grid[d][5] and not grid[d][18]
        assert not any(grid[5]) and not any(grid[6])  # sam / dim fermés
    def test_lsb_first_in_byte(self):
        # dimanche 00h → bit 0 du premier octet ; dimanche 07h → bit 7
        assert encode_logon_hours(grid_with({6: [0]}))[0] == 0x01
        assert encode_logon_hours(grid_with({6: [7]}))[0] == 0x80
        # lundi 00h → 1er octet du 2e jour (jour stockage 1 = octet 3)
        assert encode_logon_hours(grid_with({0: [0]}))[3] == 0x01

    def test_output_is_21_bytes(self):
        assert len(encode_logon_hours(new_grid(True))) == TOTAL_BYTES
        assert TOTAL_BITS == 168

    def test_decode_none_is_unrestricted(self):
        assert is_unrestricted(decode_logon_hours(None))
        assert is_unrestricted(decode_logon_hours(b""))

    def test_decode_all_zero_is_empty(self):
        assert is_empty(decode_logon_hours(bytes(TOTAL_BYTES)))

    def test_round_trip_without_offset(self):
        grid = grid_with({0: [8, 9, 10], 2: list(range(14, 20)), 5: [9, 10]})
        data = encode_logon_hours(grid)
        assert decode_logon_hours(data) == grid

    def test_round_trip_with_offsets(self):
        grid = grid_with({0: list(range(8, 18)), 4: [7], 6: [10, 11, 12]})
        for offset in (-5, -1, 0, 1, 2, 3, 7):
            data = encode_logon_hours(grid, utc_offset_hours=offset)
            assert decode_logon_hours(data, utc_offset_hours=offset) == grid, offset


class TestTimezoneConversion:
    def test_cet_monday_8_local_is_monday_7_utc(self):
        # UTC+1 : 08:00 local Lundi = 07:00 UTC Lundi → octet 3 bit 7
        grid = grid_with({0: [8]})
        data = encode_logon_hours(grid, utc_offset_hours=1)
        assert data[3] == 0x80

    def test_cet_decode_back(self):
        data = encode_logon_hours(grid_with({0: [8]}), utc_offset_hours=1)
        grid = decode_logon_hours(data, utc_offset_hours=1)
        assert grid[0][8] and not grid[0][7]

    def test_offset_shifts_across_midnight(self):
        # UTC+1 : 00:30 local lundi = 23:30 UTC dimanche → jour stockage 0
        grid = grid_with({0: [0]})
        data = encode_logon_hours(grid, utc_offset_hours=1)
        assert data[2] == 0x80          # dimanche 23h (bit 7 de l'octet 2)
        assert data[3] == 0x00          # lundi inchangé

    def test_zero_offset_is_identity(self):
        grid = grid_with({1: [12]})
        assert encode_logon_hours(grid, utc_offset_hours=0) == encode_logon_hours(grid)

    def test_current_offset_is_int(self):
        offset = current_utc_offset_hours()
        assert isinstance(offset, int)
        assert -12 <= offset <= 14


class TestPresets:
    def test_builtin_presets(self):
        names = [p.name for p in LogonHoursPreset.builtin()]
        assert names == ["Heures cours", "Heures admin", "Personnalisé"]

    def test_heures_cours(self):
        preset = next(p for p in LogonHoursPreset.builtin() if p.name == "Heures cours")
        grid = preset.build()
        for d in range(5):  # lun-ven 8h→18h
            assert grid[d][8] and grid[d][17]
            assert not grid[d][7] and not grid[d][18]
        assert grid[5][8] and grid[5][11] and not grid[5][12]  # sam 8h→12h
        assert not any(grid[6])  # dimanche fermé

    def test_heures_admin(self):
        preset = next(p for p in LogonHoursPreset.builtin() if p.name == "Heures admin")
        grid = preset.build()
        assert grid[0][7] and grid[0][19] and not grid[0][20]  # lun 7h→20h
        assert grid[5][9] and grid[5][12] and not grid[5][13]  # sam 9h→13h
        assert not any(grid[6])

    def test_perso_has_no_range(self):
        preset = next(p for p in LogonHoursPreset.builtin() if p.name == "Personnalisé")
        assert preset.ranges == ()
        assert is_empty(preset.build())  # grille vierge = édition manuelle


class TestSummary:
    def test_unrestricted(self):
        assert "Toutes heures" in grid_summary(new_grid(True))

    def test_empty(self):
        assert grid_summary(new_grid(False)) == "Aucune heure autorisée"

    def test_weekdays_grouped(self):
        grid = LogonHoursPreset.builtin()[0].build()  # Heures cours
        text = grid_summary(grid)
        assert "Lundi–Vendredi 08:00-18:00" in text
        assert "Samedi 08:00-12:00" in text
        assert "Dimanche fermé" in text

    def test_split_ranges_same_day(self):
        grid = grid_with({0: [8, 9, 10, 11, 14, 15]})
        assert "Lundi 08:00-12:00, 14:00-16:00" in grid_summary(grid)


class _FakeConnection:
    """Connexion AD factice pour LogonHoursManager."""

    def __init__(self) -> None:
        self.store: dict[str, bytes | None] = {}
        self.calls: list[str] = []

    def get_logon_hours(self, user_dn: str) -> bytes | None:
        self.calls.append(f"get:{user_dn}")
        return self.store.get(user_dn)

    def set_logon_hours(self, user_dn: str, value: bytes) -> None:
        self.calls.append(f"set:{user_dn}")
        self.store[user_dn] = value

    def clear_logon_hours(self, user_dn: str) -> None:
        self.calls.append(f"clear:{user_dn}")
        self.store[user_dn] = None


class TestManager:
    def _grid(self):
        return grid_with({0: list(range(8, 18))})

    def test_get_grid_absent_attribute_is_unrestricted(self):
        mgr = LogonHoursManager(_FakeConnection(), utc_offset_hours=0)
        assert is_unrestricted(mgr.get_grid("CN=x"))

    def test_apply_sets_attribute(self):
        fake = _FakeConnection()
        mgr = LogonHoursManager(fake, utc_offset_hours=0)
        assert mgr.apply("CN=x", self._grid()) == "set"
        assert fake.store["CN=x"] == encode_logon_hours(self._grid())

    def test_apply_is_idempotent(self):
        fake = _FakeConnection()
        mgr = LogonHoursManager(fake, utc_offset_hours=0)
        mgr.apply("CN=x", self._grid())
        fake.calls.clear()
        assert mgr.apply("CN=x", self._grid()) == "none"
        assert fake.calls == ["get:CN=x"]  # pas de réécriture inutile

    def test_apply_unrestricted_clears_attribute(self):
        fake = _FakeConnection()
        mgr = LogonHoursManager(fake, utc_offset_hours=0)
        mgr.apply("CN=x", self._grid())
        assert mgr.apply("CN=x", new_grid(True)) == "clear"
        assert fake.store["CN=x"] is None

    def test_apply_unrestricted_when_absent_is_noop(self):
        fake = _FakeConnection()
        mgr = LogonHoursManager(fake, utc_offset_hours=0)
        # La lecture préalable évite un MODIFY_DELETE inutile (attribut absent)
        assert mgr.apply("CN=x", new_grid(True)) == "none"
        assert fake.calls == ["get:CN=x"]

    def test_get_after_apply_round_trips(self):
        fake = _FakeConnection()
        mgr = LogonHoursManager(fake, utc_offset_hours=1)
        grid = self._grid()
        mgr.apply("CN=x", grid)
        assert mgr.get_grid("CN=x") == grid
