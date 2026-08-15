"""Groups retire when their page is deleted or moved into an archive."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.collective_page import CollectivePage
from app.models.group import Group
from app.models.group_role import GroupRole
from app.services.config import OrganisationConfig

PARSER = "app.services.collectives_parser"

JAN = 1735689600  # 2025-01-01
MAR = 1740787200  # 2025-03-01


@pytest.fixture
def mock_bot_config():
    config = MagicMock()
    config.organisation = OrganisationConfig()
    return config


@pytest.fixture(autouse=True)
def patched_config(mock_bot_config):
    with patch("app.models.group.bot_config", mock_bot_config):
        yield mock_bot_config


def page(page_id: int, title: str, file_path: str) -> CollectivePage:
    return CollectivePage(
        page_id=page_id,
        title=title,
        file_path=file_path,
        file_name="README.md",
        timestamp=JAN,
    )


class TestIsArchivedPath:
    @pytest.mark.parametrize(
        "path",
        [
            "Archiv/AG Haus",
            "archiv/AG Haus",
            "Koordinationskreis/Archiv/AG Haus",
            "Archiv 2024/AG Haus",
            "Archiv-2024/AG Haus",
            "Archive/AG Haus",
            # a whole branch: the subgroup below an archived group
            "Archiv/AG Haus/UG Keller",
        ],
    )
    def test_archived_paths(self, path):
        assert Group.is_archived_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "AG Haus",
            "Koordinationskreis/AG Haus",
            # not an archive page, just a similar name
            "Archivierung/AG Haus",
            "Archivwesen/AG Haus",
            "AG Archivwesen",
            "",
        ],
    )
    def test_regular_paths(self, path):
        assert Group.is_archived_path(path) is False

    def test_names_are_configurable(self, patched_config):
        patched_config.organisation.archive_page_names = ["ablage"]

        assert Group.is_archived_path("Ablage/AG Haus") is True
        assert Group.is_archived_path("Archiv/AG Haus") is False

    def test_empty_names_disable_the_check(self, patched_config):
        patched_config.organisation.archive_page_names = []

        assert Group.is_archived_path("Archiv/AG Haus") is False


class TestParseGroups:
    def test_archived_group_page_is_not_parsed(self, patched_config):
        from app.services.collectives_parser import parse_groups

        archived = page(1, "AG Haus", "Archiv/AG Haus")
        archived.content = "# AG Haus"
        archived.subtype = "group"

        with patch(f"{PARSER}.bot_config", patched_config):
            with patch.object(Group, "fetch_one") as fetch_one:
                with patch.object(GroupRole, "sync_group") as sync_group:
                    parse_groups(archived)

        fetch_one.assert_not_called()
        sync_group.assert_not_called()


class TestRetireStaleGroups:
    """A retired group is marked inactive, not deleted: its membership and
    dates are what a link from a past role has to land on."""

    def _run(self, groups, pages, patched_config):
        from app.services.collectives_parser import retire_stale_groups

        retired = []
        with patch(f"{PARSER}.bot_config", patched_config):
            with patch.object(Group, "fetch", return_value=groups):
                with patch.object(CollectivePage, "fetch", return_value=pages):
                    with patch.object(Group, "remove", autospec=True) as remove:
                        with patch.object(Group, "store", autospec=True):
                            with patch.object(GroupRole, "close_for_page") as close:
                                retire_stale_groups()
        assert remove.call_count == 0, "groups are kept, only marked inactive"
        retired = [g for g in groups if not g.is_active]
        self.closed_pages = [call.args[0] for call in close.call_args_list]
        return retired

    def test_group_without_a_page_is_retired(self, patched_config):
        group = Group(name="AG Haus", page_id=1)

        retired = self._run([group], [], patched_config)

        assert retired == [group]
        assert group.end_date is not None
        # its open roles end with it, the history stays
        assert self.closed_pages == [1]

    def test_archived_group_and_its_subgroup_are_retired(self, patched_config):
        haus = Group(name="AG Haus", page_id=1)
        keller = Group(name="UG Keller", page_id=2, parent_group="AG Haus")
        garten = Group(name="AG Garten", page_id=3)
        pages = [
            page(1, "AG Haus", "Archiv/AG Haus"),
            page(2, "UG Keller", "Archiv/AG Haus/UG Keller"),
            page(3, "AG Garten", "Koordinationskreis/AG Garten"),
        ]

        retired = self._run([haus, keller, garten], pages, patched_config)

        assert retired == [haus, keller]
        assert garten.is_active

    def test_active_groups_are_kept(self, patched_config):
        group = Group(name="AG Garten", page_id=3)
        pages = [page(3, "AG Garten", "Koordinationskreis/AG Garten")]

        assert self._run([group], pages, patched_config) == []

    def test_an_already_retired_group_is_not_touched_again(self, patched_config):
        group = Group(name="AG Haus", page_id=1, end_date=JAN)

        self._run([group], [], patched_config)

        assert group.end_date == JAN
        assert self.closed_pages == []

    def test_the_retired_group_keeps_its_membership(self, patched_config):
        group = Group(name="AG Haus", page_id=1, coordination=["alice"])

        self._run([group], [], patched_config)

        assert group.coordination == ["alice"]


class TestRetire:
    def test_the_end_date_is_never_before_the_start(self):
        group = Group(name="AG Haus", page_id=1, start_date=MAR)

        with patch.object(Group, "store"):
            with patch.object(GroupRole, "close_for_page"):
                group.retire(timestamp=JAN)

        assert group.end_date == MAR

    def test_parsing_the_page_again_revives_the_group(self, patched_config):
        group = Group(name="AG Haus", page_id=1, end_date=JAN, start_date=JAN)
        revived = page(1, "AG Haus", "Koordinationskreis/AG Haus")
        revived.content = "# AG Haus"

        with patch.object(CollectivePage, "fetch_one", return_value=revived):
            with patch.object(Group, "store"):
                group.update_from_page()

        assert group.is_active
        # the original creation date survives the round trip
        assert group.start_date == JAN

    def test_a_new_group_is_dated_by_its_page(self, patched_config):
        group = Group(page_id=1)
        source = page(1, "AG Haus", "Koordinationskreis/AG Haus")
        source.content = "# AG Haus"

        with patch.object(CollectivePage, "fetch_one", return_value=source):
            with patch.object(Group, "store"):
                group.update_from_page()

        assert group.start_date == JAN
        assert group.is_active


class TestMoveDetection:
    """A moved page must be re-stored even when its mtime did not change."""

    def _ocs(self, title: str, file_path: str):
        from app.models.collective_page import OCSCollectivePage

        return OCSCollectivePage(
            id=1, title=title, filePath=file_path, fileName="README.md", timestamp=JAN
        )

    def test_changed_path_counts_as_a_change(self):
        from app.services.collectives_loader import _moved

        stored = page(1, "AG Haus", "Koordinationskreis/AG Haus")

        assert _moved(stored, self._ocs("AG Haus", "Archiv/AG Haus")) is True

    def test_changed_title_counts_as_a_change(self):
        from app.services.collectives_loader import _moved

        stored = page(1, "AG Haus", "Koordinationskreis/AG Haus")

        assert (
            _moved(stored, self._ocs("AG Hütte", "Koordinationskreis/AG Haus")) is True
        )

    def test_same_location_is_no_change(self):
        from app.services.collectives_loader import _moved

        stored = page(1, "AG Haus", "Koordinationskreis/AG Haus")

        assert (
            _moved(stored, self._ocs("AG Haus", "Koordinationskreis/AG Haus")) is False
        )


class TestRolesSurviveRetirement:
    def test_open_roles_are_ended_and_history_is_kept(self):
        group = Group(name="AG Haus", page_id=1)
        rows = [
            GroupRole(
                username="alice",
                group_name="AG Haus",
                page_id=1,
                role="coordination",
                start_date=JAN,
            ),
            GroupRole(
                username="bob",
                group_name="AG Haus",
                page_id=1,
                role="member",
                start_date=JAN,
                end_date=JAN + 86400,
            ),
        ]

        stored = []
        with patch.object(GroupRole, "for_group_page", return_value=rows):
            with patch.object(GroupRole, "store", autospec=True) as store:
                store.side_effect = lambda self, **kw: stored.append(self)
                group.before_remove()

        # alice's open role is closed, bob's finished period is untouched
        assert stored == [rows[0]]
        assert rows[0].end_date is not None
        assert rows[0].start_date == JAN
        assert rows[1].end_date == JAN + 86400


class TestDedupeShortNames:
    """One-off repair of short name lists stored before they were deduped."""

    def _run(self, groups):
        from app.services.collectives_parser import dedupe_short_names

        stored = []
        with (
            patch.object(Group, "fetch", return_value=groups),
            patch("app.models.base.BaseDBModel.store", autospec=True) as store,
        ):
            store.side_effect = lambda self, **kwargs: stored.append(self.name)
            dedupe_short_names()
        return stored

    def test_duplicates_are_collapsed_and_sorted(self):
        group = Group(
            name="AG Haus", page_id=1, short_names=["haus", "ag-haus", "haus"]
        )

        assert self._run([group]) == ["AG Haus"]
        assert group.short_names == ["ag-haus", "haus"]

    def test_clean_groups_are_not_rewritten(self):
        clean = Group(name="AG Garten", page_id=2, short_names=["garten"])
        empty = Group(name="AG Leer", page_id=3, short_names=[])

        assert self._run([clean, empty]) == []

    def test_a_second_pass_writes_nothing(self):
        group = Group(name="AG Haus", page_id=1, short_names=["b", "a", "b"])

        self._run([group])

        assert self._run([group]) == []
