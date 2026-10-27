from app.api.fournos import _live_job_to_summary


def test_live_job_summary_includes_requested_gpu_hardware():
    job = {
        "metadata": {"name": "rhaiis-hera-example", "labels": {}},
        "spec": {
            "cluster": "hera",
            "pipeline": "forge-full",
            "hardware": {"gpuType": "h200", "gpuCount": 8},
            "executionEngine": {
                "forge": {"project": "rhaiis", "args": ["qwen", "vllm"]}
            },
        },
        "status": {"phase": "Running"},
    }

    summary = _live_job_to_summary(job)

    assert summary["gpu_type"] == "h200"
    assert summary["gpu_count"] == 8
