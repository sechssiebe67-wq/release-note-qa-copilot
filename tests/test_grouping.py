import unittest

from releasenote.grouping import group_entries, group_titles
from releasenote.models import (
    GROUP_ORDER,
    TYPE_BREAKING,
    TYPE_FEATURE,
    TYPE_FIX,
    TYPE_OTHER,
    ChangeEntry,
)


class GroupingTests(unittest.TestCase):
    def test_groups_by_type(self):
        entries = [
            ChangeEntry(id="1", type=TYPE_FEATURE),
            ChangeEntry(id="2", type=TYPE_FIX),
            ChangeEntry(id="3", type=TYPE_FEATURE),
            ChangeEntry(id="4", type=TYPE_BREAKING),
        ]
        groups = group_entries(entries)
        self.assertEqual([e.id for e in groups[TYPE_FEATURE]], ["1", "3"])
        self.assertEqual([e.id for e in groups[TYPE_FIX]], ["2"])
        self.assertEqual([e.id for e in groups[TYPE_BREAKING]], ["4"])

    def test_all_groups_present(self):
        groups = group_entries([])
        self.assertEqual(set(groups), set(GROUP_ORDER))

    def test_unknown_type_falls_back_to_other(self):
        entries = [ChangeEntry(id="9", type="weird")]
        groups = group_entries(entries)
        self.assertEqual([e.id for e in groups[TYPE_OTHER]], ["9"])

    def test_preserves_order(self):
        entries = [ChangeEntry(id=str(i), type=TYPE_FIX) for i in range(5)]
        groups = group_entries(entries)
        self.assertEqual([e.id for e in groups[TYPE_FIX]], ["0", "1", "2", "3", "4"])

    def test_titles(self):
        titles = group_titles()
        self.assertEqual(titles[TYPE_FEATURE], "Features")
        self.assertEqual(titles[TYPE_BREAKING], "Breaking Changes")
        # Returned mapping is a copy, not shared state.
        titles[TYPE_FEATURE] = "changed"
        self.assertEqual(group_titles()[TYPE_FEATURE], "Features")


if __name__ == "__main__":
    unittest.main()
