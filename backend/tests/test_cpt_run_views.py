from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from app.services.cpt_run_views import (
    build_cpt_run_groups,
    build_new_cpt_attempt,
    filter_cpt_run_groups,
    redact_cpt_group_owners,
)


def _job(name, run_id, created_at, status, workloads, **dimensions):
    metadata = {
        "project": "rhaiis",
        "pipeline_key": "cpt-rhaiis-mi355x",
        "version_label": "vLLM-0.26.0",
        "cluster": "mi355x",
        "accelerator": "amd",
        "engine": "vllm",
        "engine_version": "vllm:v0.26.0",
        "fournos_pipeline": "forge-full",
        "model_key": "gpt-oss-120b",
        "model_name": "gpt-oss-120b",
        "workloads": workloads,
        "configured_model_count": 1,
        "owner": "runner",
        "started_at": created_at.isoformat(),
        **dimensions,
    }
    return SimpleNamespace(
        name=name,
        cpt_run_id=run_id,
        cpt_run_metadata=metadata,
        cpt_stop_requested_at=None,
        cpt_stop_message="",
        status=status,
        message="",
        owner="runner",
        preset="gpt-oss-120b",
        created_at=created_at,
        fjob_spec={
            "executionEngine": {
                "forge": {
                    "project": "rhaiis",
                    "args": ["amd", "vllm", "mi355x", "gpt-oss-120b", "profile1"],
                    "configOverrides": {
                        "tests.rhaiis.version": "vLLM-0.26.0",
                        "tests.rhaiis.workload_keys": '["profile1"]',
                    },
                }
            }
        },
    )


class CptRunViewsTests(unittest.TestCase):
    def test_latest_result_rolls_forward_per_model_and_profile(self):
        older = datetime(2026, 10, 1, tzinfo=timezone.utc)
        newer = datetime(2026, 10, 2, tzinfo=timezone.utc)
        groups = build_cpt_run_groups([
            _job("old", "run-old", older, "Succeeded", ["profile4", "profile1"]),
            _job("new", "run-new", newer, "Failed", ["profile6"]),
        ])

        self.assertEqual(len(groups), 1)
        group = groups[0]
        cells = group["models"][0]["cells"]
        self.assertEqual(cells["profile1"]["job_name"], "old")
        self.assertEqual(cells["profile4"]["status"], "Succeeded")
        self.assertEqual(cells["profile6"]["status"], "Failed")
        self.assertEqual(group["profiles"], ["profile1", "profile4", "profile6"])
        self.assertEqual(group["status"], "Partially failed")

    def test_group_dimensions_keep_engine_versions_separate(self):
        created = datetime(2026, 10, 1, tzinfo=timezone.utc)
        groups = build_cpt_run_groups([
            _job("one", "run-1", created, "Succeeded", ["profile1"]),
            _job(
                "two", "run-2", created, "Succeeded", ["profile1"],
                engine_version="vllm:v0.28.0",
            ),
        ])

        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {group["engine_version"] for group in groups},
            {"vllm:v0.26.0", "vllm:v0.28.0"},
        )

    def test_stop_request_hides_group_until_user_shows_stopped(self):
        created = datetime(2026, 10, 1, tzinfo=timezone.utc)
        job = _job("active", "run-1", created, "Running", ["profile1"])
        job.cpt_stop_requested_at = created
        groups = build_cpt_run_groups([job])

        self.assertEqual(groups[0]["status"], "Stopped")
        self.assertEqual(groups[0]["active_jobs"], [])
        self.assertEqual(filter_cpt_run_groups(groups), [])
        self.assertEqual(len(filter_cpt_run_groups(groups, show_stopped=True)), 1)

    def test_rerunning_a_cpt_child_creates_a_new_rolling_attempt(self):
        created = datetime(2026, 10, 1, tzinfo=timezone.utc)
        source = _job("source", "run-old", created, "Failed", ["profile1"])
        edited_spec = {
            "cluster": "mi355x",
            "pipeline": "forge-full",
            "executionEngine": {
                "forge": {
                    "project": "rhaiis",
                    "args": ["amd", "vllm", "mi355x", "gpt-oss-120b", "profile6"],
                    "configOverrides": {
                        "tests.rhaiis.version": "vLLM-0.28.0",
                        "tests.rhaiis.workload_keys": '["profile6"]',
                        "rhaiis.engines.vllm.images.amd": "vllm:v0.28.0",
                    },
                }
            },
        }

        run_id, metadata = build_new_cpt_attempt(
            source, edited_spec, "new-owner", now=created
        )

        self.assertNotEqual(run_id, "run-old")
        self.assertEqual(metadata["pipeline_key"], "cpt-rhaiis-mi355x")
        self.assertEqual(metadata["version_label"], "vLLM-0.28.0")
        self.assertEqual(metadata["engine_version"], "vllm:v0.28.0")
        self.assertEqual(metadata["workloads"], ["profile6"])
        self.assertEqual(metadata["owner"], "new-owner")

    def test_ordinary_job_does_not_get_cpt_tracking(self):
        ordinary = SimpleNamespace(cpt_run_id=None, cpt_run_metadata={})
        self.assertEqual(
            build_new_cpt_attempt(ordinary, {"cluster": "hera"}, "owner"),
            (None, {}),
        )

    def test_redact_cpt_group_owners_returns_copy_and_hides_attempt_owners(self):
        group = {
            "owners": ["runner"],
            "owner_label": "runner",
            "attempts": [{"id": "run-1", "owner": "runner"}],
            "status": "Succeeded",
        }

        public_group = redact_cpt_group_owners(group)

        self.assertEqual(public_group["owners"], [])
        self.assertEqual(public_group["owner_label"], "")
        self.assertEqual(public_group["attempts"], [{"id": "run-1", "owner": ""}])
        self.assertEqual(public_group["status"], "Succeeded")
        self.assertEqual(group["owners"], ["runner"])
        self.assertEqual(group["attempts"][0]["owner"], "runner")

    def test_failed_to_submit_models_remain_visible_but_not_counted_as_submitted(self):
        created = datetime(2026, 10, 1, tzinfo=timezone.utc)
        submitted = _job("model-a-job", "run-1", created, "Succeeded", ["profile1"])
        failed_metadata = dict(submitted.cpt_run_metadata)
        failed_metadata.update({
            "model_key": "model-b",
            "model_name": "model-b",
            "model_preset": "model-b",
            "workloads": ["profile6"],
        })
        not_submitted = SimpleNamespace(
            name="",
            job_name=None,
            cpt_run_id="run-1",
            cpt_run_metadata=failed_metadata,
            submission_status="Failed",
            status="Failed",
            message="Kubernetes rejected the job",
            owner="runner",
            preset="model-b",
            created_at=created,
            cpt_stop_requested_at=None,
        )

        group = build_cpt_run_groups([submitted, not_submitted])[0]

        self.assertEqual(group["submitted_model_count"], 1)
        self.assertEqual(group["not_submitted_model_count"], 1)
        self.assertEqual(group["attempts"][0]["job_count"], 2)
        model = next(item for item in group["models"] if item["model_key"] == "model-b")
        self.assertEqual(model["cells"]["profile6"]["status"], "Not submitted")
        self.assertIsNone(model["cells"]["profile6"]["job_name"])


if __name__ == "__main__":
    unittest.main()
