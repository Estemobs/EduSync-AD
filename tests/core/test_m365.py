"""Tests de la logique métier Office 365 / Entra ID (M19)."""

from __future__ import annotations

from pathlib import Path

import pytest

from edusync_ad.core.graph import GraphConfig, GraphError
from edusync_ad.core.m365 import (
    DEFAULT_LICENSE,
    EDUCATION_LICENSES,
    GROUP_KINDS,
    CloudUserPlan,
    GroupPlan,
    M365Manager,
    build_group_plans,
    build_photo_mapping,
    build_user_plans,
    cloud_password_policy,
    hybrid_checklist,
    hybrid_checklist_markdown,
    mail_nickname,
    split_display_name,
)

DOMAIN = "lycee.local"


class FakeM365Client:
    """Faux client Graph : enregistre les appels, simule l'état du locataire."""

    def __init__(
        self,
        *,
        users: list[dict] | None = None,
        groups: list[dict] | None = None,
        skus: dict[str, str] | None = None,
        fail_create_user: bool = False,
        fail_create_group: bool = False,
        existing_members: set[str] | None = None,
    ) -> None:
        self.users = list(users or [])
        self.groups = list(groups or [])
        self.skus = skus if skus is not None else {
            "M365EDU_A1": "sku-a1",
            "M365EDU_A3_FACULTY": "sku-a3",
        }
        self.fail_create_user = fail_create_user
        self.fail_create_group = fail_create_group
        self.existing_members = existing_members or set()
        self.calls: list[tuple] = []
        self._seq = 0

    def organization(self) -> dict:
        return {"displayName": "Lycée de Test"}

    def list_users(self) -> list[dict]:
        return list(self.users)

    def list_groups(self) -> list[dict]:
        return list(self.groups)

    def subscribed_skus(self) -> dict[str, str]:
        return dict(self.skus)

    def create_user(self, **kwargs) -> dict:
        if self.fail_create_user:
            raise GraphError("UPN déjà utilisé", 409, "Request_BadRequest")
        self.calls.append(("create_user", kwargs))
        self._seq += 1
        user = {"id": f"u-{self._seq}", "userPrincipalName": kwargs["user_principal_name"]}
        self.users.append(user)
        return user

    def assign_license(self, user_id: str, sku_id: str) -> dict:
        self.calls.append(("assign_license", user_id, sku_id))
        return {"id": user_id}

    def create_group(self, **kwargs) -> dict:
        if self.fail_create_group:
            raise GraphError("mailNickname déjà utilisé", 400, "Request_BadRequest")
        self.calls.append(("create_group", kwargs))
        self._seq += 1
        group = {
            "id": f"g-{self._seq}",
            "displayName": kwargs["display_name"],
            "mailNickname": kwargs["mail_nickname"],
        }
        self.groups.append(group)
        return group

    def add_group_member(self, group_id: str, member_id: str) -> None:
        key = (group_id, member_id)
        if key in self.existing_members:
            raise GraphError("one or more added objects already exist", 400)
        self.existing_members.add(key)
        self.calls.append(("add_group_member", group_id, member_id))

    def create_team(self, group_id: str) -> None:
        self.calls.append(("create_team", group_id))

    def upload_user_photo(self, user_id: str, jpeg_bytes: bytes) -> None:
        self.calls.append(("upload_photo", user_id, jpeg_bytes))


def make_manager(client: FakeM365Client) -> M365Manager:
    return M365Manager(
        config=GraphConfig(
            tenant_id="11111111-2222-3333-4444-555555555555",
            client_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            client_secret="x",
            usage_location="FR",
        ),
        client=client,
    )


# -- Helpers ----------------------------------------------------------------------------

class TestHelpers:
    def test_mail_nickname_strips_accents_and_spaces(self):
        assert mail_nickname("6ème A") == "6emea"
        assert mail_nickname("Terminale S – Projet") == "terminalesprojet"

    def test_mail_nickname_removes_reserved_chars(self):
        assert mail_nickname("classe #1 & 2") == "classe12"

    def test_mail_nickname_never_empty(self):
        assert mail_nickname("???") == "groupe"

    def test_mail_nickname_idempotent(self):
        assert mail_nickname(mail_nickname("6ème A")) == mail_nickname("6ème A")

    def test_split_display_name(self):
        assert split_display_name("Jean Dupont") == ("Jean", "Dupont")
        assert split_display_name("Camille De La Croix") == ("Camille", "De La Croix")

    def test_split_display_name_strips_duplicate_suffix(self):
        assert split_display_name("Paul Martin (pmartin)", "pmartin") == ("Paul", "Martin")

    def test_split_display_name_single_word(self):
        assert split_display_name("Chercheur") == ("Chercheur", "")

    def test_cloud_password_policy_strength(self):
        policy = cloud_password_policy()
        assert policy.longueur >= 16
        assert policy.majuscules and policy.chiffres and policy.caracteres_speciaux

    def test_license_catalog(self):
        assert DEFAULT_LICENSE in EDUCATION_LICENSES
        assert "A1" in EDUCATION_LICENSES["M365EDU_A1"]
        assert set(GROUP_KINDS) == {"m365", "distribution", "security"}


# -- Plans --------------------------------------------------------------------------------

class TestUserPlans:
    def _users(self) -> list[dict]:
        return [
            {"sam": "jdurand", "cn": "Jean Durand", "disabled": False},
            {"sam": "amartin", "cn": "Alice Martin (amartin)", "disabled": True},
            {"sam": "", "cn": "Incomplet"},
        ]

    def test_upn_and_names(self):
        plans = build_user_plans(self._users(), DOMAIN)
        assert len(plans) == 2  # ligne sans sam ignorée
        assert plans[0].upn == "jdurand@lycee.local"
        assert (plans[0].prenom, plans[0].nom) == ("Jean", "Durand")
        assert plans[1].display_name == "Alice Martin (amartin)"
        assert plans[1].disabled is True

    def test_existing_flag(self):
        plans = build_user_plans(self._users(), DOMAIN, existing_upns=["JDURAND@LYCEE.LOCAL"])
        assert plans[0].existing is True
        assert plans[1].existing is False

    def test_label_shows_status(self):
        plans = build_user_plans(self._users(), DOMAIN, existing_upns=["jdurand@lycee.local"])
        assert "[existant]" in plans[0].label
        assert "[à créer]" in plans[1].label


class TestGroupPlans:
    def test_builds_unique_plans(self):
        specs = [
            {"name": "6ème A", "members": ["a@l", "b@l"]},
            {"name": "6eme A", "members": ["c@l"]},  # même mailNickname après slug
        ]
        plans = build_group_plans(specs, kind="distribution")
        assert len(plans) == 1
        assert plans[0].kind == "distribution"
        assert plans[0].members == ["a@l", "b@l"]

    def test_existing_detection(self):
        plans = build_group_plans(
            [{"name": "Profs", "members": []}],
            kind="m365",
            existing_by_nickname={"profs": "g-99"},
        )
        assert plans[0].existing_id == "g-99"
        assert "[existant" in plans[0].label

    def test_empty_name_skipped(self):
        assert build_group_plans([{"name": "   ", "members": []}]) == []


class TestPhotoMapping:
    def test_maps_files_to_upn(self, tmp_path):
        from PIL import Image

        Image.new("RGB", (60, 60), "red").save(tmp_path / "jdurand.jpg")
        Image.new("RGB", (60, 60), "blue").save(tmp_path / "alice_martin.png")

        mapping = build_photo_mapping(
            tmp_path,
            [
                {"sam": "jdurand", "cn": "Jean Durand"},
                {"sam": "amartin", "cn": "Alice Martin"},
                {"sam": "autre", "cn": "Autre Élève"},
            ],
            DOMAIN,
        )
        assert mapping == {
            "jdurand@lycee.local": tmp_path / "jdurand.jpg",
            "amartin@lycee.local": tmp_path / "alice_martin.png",
        }

    def test_no_files_gives_empty_mapping(self, tmp_path):
        assert build_photo_mapping(tmp_path, [{"sam": "x", "cn": "X Y"}], DOMAIN) == {}


# -- Manager -----------------------------------------------------------------------

class TestManagerConnection:
    def test_test_connection_returns_tenant_name(self):
        manager = make_manager(FakeM365Client())
        assert manager.test_connection() == "Lycée de Test"

    def test_available_licenses_filtered_on_catalog(self):
        client = FakeM365Client(
            skus={"M365EDU_A1": "sku-a1", "EXCHANGESTANDARD": "sku-exch"}
        )
        licenses = make_manager(client).available_licenses()
        assert list(licenses) == ["M365EDU_A1"]
        assert licenses["M365EDU_A1"]["sku_id"] == "sku-a1"
        assert "A1" in licenses["M365EDU_A1"]["label"]

    def test_unknown_license_raises_before_any_creation(self):
        client = FakeM365Client(skus={"M365EDU_A1": "sku-a1"})
        manager = make_manager(client)
        plans = [CloudUserPlan("a", "a@l", "A", "A", "A A")]
        with pytest.raises(GraphError, match="Licence absente"):
            manager.sync_users(plans, "M365EDU_A5_FACULTY")
        assert client.calls == []


class TestSyncUsers:
    def _plans(self) -> list[CloudUserPlan]:
        return [
            CloudUserPlan("jdurand", "jdurand@lycee.local", "Jean", "Durand", "Jean Durand"),
            CloudUserPlan(
                "amartin", "amartin@lycee.local", "Alice", "Martin", "Alice Martin",
                existing=True,
            ),
        ]

    def test_creates_missing_account_and_assigns_license(self):
        client = FakeM365Client()
        manager = make_manager(client)
        results = manager.sync_users(self._plans(), "M365EDU_A1")

        created = [r for r in results if r.action == "created"]
        assert len(created) == 1
        assert "licence" in created[0].detail

        create_call = next(c for c in client.calls if c[0] == "create_user")
        kwargs = create_call[1]
        assert kwargs["account_enabled"] is True
        assert kwargs["usage_location"] == "FR"
        assert kwargs["given_name"] == "Jean"
        assert len(kwargs["password"]) >= 16  # mot de passe fort généré

        license_call = next(c for c in client.calls if c[0] == "assign_license")
        assert license_call[2] == "sku-a1"

    def test_existing_account_is_never_touched(self):
        client = FakeM365Client()
        manager = make_manager(client)
        results = manager.sync_users(self._plans(), "M365EDU_A1")

        existing = [r for r in results if r.key == "amartin@lycee.local"]
        assert existing[0].action == "exists"
        assert not any(
            c[0] == "create_user"
            and c[1]["user_principal_name"] == "amartin@lycee.local"
            for c in client.calls
        )
        # seul le compte manquant est créé (+ sa licence)
        assert len([c for c in client.calls if c[0] == "create_user"]) == 1
        assert len([c for c in client.calls if c[0] == "assign_license"]) == 1

    def test_without_license_no_assign_call(self):
        client = FakeM365Client()
        make_manager(client).sync_users(self._plans(), "")
        assert not any(c[0] == "assign_license" for c in client.calls)

    def test_creation_failure_is_reported_not_raised(self):
        client = FakeM365Client(fail_create_user=True)
        results = make_manager(client).sync_users(self._plans(), "M365EDU_A1")
        errors = [r for r in results if r.action == "error"]
        assert len(errors) == 1
        assert "UPN déjà utilisé" in errors[0].detail
        assert not errors[0].ok
        # le reste du lot continue d'être traité
        assert results[1].action == "exists"
        assert results[1].ok

    def test_disabled_ad_account_stays_disabled(self):
        client = FakeM365Client()
        plans = [CloudUserPlan("x", "x@l", "X", "Y", "X Y", disabled=True)]
        make_manager(client).sync_users(plans, "")
        kwargs = next(c for c in client.calls if c[0] == "create_user")[1]
        assert kwargs["account_enabled"] is False

    def test_on_progress_called_per_plan(self):
        seen: list[str] = []
        make_manager(FakeM365Client()).sync_users(
            self._plans(), "", on_progress=lambda p: seen.append(p.sam)
        )
        assert seen == ["jdurand", "amartin"]


class TestSyncGroups:
    def test_creates_group_and_adds_members(self):
        client = FakeM365Client(
            users=[
                {"id": "u-1", "userPrincipalName": "a@lycee.local"},
                {"id": "u-2", "userPrincipalName": "b@lycee.local"},
            ]
        )
        manager = make_manager(client)
        plans = build_group_plans(
            [{"name": "6ème A", "members": ["a@lycee.local", "b@lycee.local", "inconnu@l"]}],
            kind="m365",
        )
        results = manager.sync_groups(plans)

        assert results[0].action == "created"
        assert "2 membre(s)" in results[0].detail
        create = next(c for c in client.calls if c[0] == "create_group")
        assert create[1]["mail_nickname"] == "6emea"
        assert create[1]["kind"] == "m365"
        added = [c for c in client.calls if c[0] == "add_group_member"]
        assert len(added) == 2  # inconnu@l ignoré (pas de compte cloud)

    def test_existing_group_is_not_recreated(self):
        client = FakeM365Client(
            groups=[{"id": "g-1", "mailNickname": "6emea"}],
            users=[{"id": "u-1", "userPrincipalName": "a@lycee.local"}],
        )
        manager = make_manager(client)
        plans = build_group_plans(
            [{"name": "6ème A", "members": ["a@lycee.local"]}],
            kind="m365",
            existing_by_nickname={"6emea": "g-1"},
        )
        results = manager.sync_groups(plans)
        assert results[0].action == "exists"
        assert not any(c[0] == "create_group" for c in client.calls)
        assert client.calls[0] == ("add_group_member", "g-1", "u-1")

    def test_team_created_only_for_new_m365_group(self):
        client = FakeM365Client()
        manager = make_manager(client)
        plans = build_group_plans([{"name": "CE1", "members": []}], kind="m365")
        manager.sync_groups(plans, create_team=True)
        assert any(c[0] == "create_team" for c in client.calls)

        # Groupe existant : pas de (re)création d'équipe
        client2 = FakeM365Client(groups=[{"id": "g-9", "mailNickname": "ce2"}])
        plans2 = build_group_plans(
            [{"name": "CE2", "members": []}], kind="m365",
            existing_by_nickname={"ce2": "g-9"},
        )
        make_manager(client2).sync_groups(plans2, create_team=True)
        assert not any(c[0] == "create_team" for c in client2.calls)

    def test_team_not_created_for_security_group(self):
        client = FakeM365Client()
        plans = build_group_plans([{"name": "RT", "members": []}], kind="security")
        make_manager(client).sync_groups(plans, create_team=True)
        assert not any(c[0] == "create_team" for c in client.calls)

    def test_creation_failure_reported(self):
        client = FakeM365Client(fail_create_group=True)
        plans = build_group_plans([{"name": "CE1", "members": []}], kind="m365")
        results = make_manager(client).sync_groups(plans)
        assert results[0].action == "error"
        assert not results[0].ok


class TestSyncPhotos:
    def _photo_file(self, tmp_path: Path, name: str) -> Path:
        from PIL import Image

        path = tmp_path / name
        Image.new("RGB", (80, 80), "green").save(path)
        return path

    def test_uploads_jpeg_for_known_user(self, tmp_path):
        path = self._photo_file(tmp_path, "x.jpg")
        client = FakeM365Client(users=[{"id": "u-1", "userPrincipalName": "x@lycee.local"}])
        results = make_manager(client).sync_photos({"x@lycee.local": path})

        assert results[0].action == "updated"
        upload = next(c for c in client.calls if c[0] == "upload_photo")
        assert upload[1] == "u-1"
        assert upload[2][:2] == b"\xff\xd8"  # bien un JPEG normalisé

    def test_unknown_account_reports_error(self, tmp_path):
        path = self._photo_file(tmp_path, "y.jpg")
        client = FakeM365Client()
        results = make_manager(client).sync_photos({"fantome@lycee.local": path})
        assert results[0].action == "error"
        assert "introuvable" in results[0].detail

    def test_missing_file_reports_error(self, tmp_path):
        client = FakeM365Client(users=[{"id": "u-1", "userPrincipalName": "x@l"}])
        results = make_manager(client).sync_photos({"x@l": tmp_path / "absent.jpg"})
        assert results[0].action == "error"


# -- Hybride -------------------------------------------------------------------

class TestHybridChecklist:
    def test_covers_phs_adfs_pta(self):
        titles = " ".join(t for t, _ in hybrid_checklist()).lower()
        assert "phs" in titles
        assert "ad fs" in titles or "adfs" in titles
        assert "pta" in titles

    def test_each_step_has_detail(self):
        assert all(title and detail for title, detail in hybrid_checklist())

    def test_markdown_export(self):
        md = hybrid_checklist_markdown()
        assert md.startswith("# Identité hybride")
        assert "Entra Connect" in md
        assert md.count("## ") == len(hybrid_checklist())
