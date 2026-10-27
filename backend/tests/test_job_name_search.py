import unittest

from app.services.job_name_search import compile_job_name_pattern


class JobNameSearchTests(unittest.TestCase):
    def test_partial_case_insensitive_regex_matches(self):
        pattern = compile_job_name_pattern("JQL")
        self.assertTrue(pattern.search("rhaiis-mi355x-jql9m"))

    def test_invalid_and_nested_repetition_are_rejected(self):
        with self.assertRaises(ValueError):
            compile_job_name_pattern("[")
        with self.assertRaises(ValueError):
            compile_job_name_pattern("(a+)+$")

    def test_pattern_length_is_bounded(self):
        with self.assertRaises(ValueError):
            compile_job_name_pattern("a" * 129)


if __name__ == "__main__":
    unittest.main()
