import base64
import unittest

from app.services import rhaiis_inference_logs as inference_logs


class RHAIISInferenceLogTests(unittest.TestCase):
    def test_reads_only_a_matching_active_forge_reference(self):
        job = {
            "metadata": {"annotations": {"rhaiis.run-uuid": "run-123"}},
            "status": {
                "engineStatus": {
                    "forge": {
                        "relevantDeployments": [{
                            "apiVersion": "serving.kserve.io/v1beta1",
                            "kind": "InferenceService",
                            "name": "model-run-123",
                            "namespace": "benchmark-ns",
                            "runUUID": "run-123",
                        }]
                    }
                }
            },
        }

        self.assertEqual(
            inference_logs.get_inference_service_reference(job),
            {"name": "model-run-123", "namespace": "benchmark-ns", "run_uuid": "run-123"},
        )
        job["status"]["engineStatus"]["forge"]["relevantDeployments"][0]["runUUID"] = "other-run"
        self.assertIsNone(inference_logs.get_inference_service_reference(job))

    def test_rejects_invalid_reference_names(self):
        job = {
            "status": {"engineStatus": {"forge": {"relevantDeployments": [{
                "kind": "InferenceService",
                "name": "../secret",
                "namespace": "benchmark-ns",
                "runUUID": "run-123",
            }]}}},
        }
        self.assertIsNone(inference_logs.get_inference_service_reference(job))

    def test_kubeconfig_secret_must_contain_encoded_kubeconfig(self):
        with self.assertRaises(inference_logs.InferenceLogsError) as raised:
            inference_logs._decode_kubeconfig(None)
        self.assertEqual(raised.exception.status_code, 503)

        raw = b"current-context: test\ncontexts:\n- name: test\n"
        kubeconfig = inference_logs._decode_kubeconfig({
            "data": {"kubeconfig": base64.b64encode(raw).decode("ascii")}
        })
        self.assertEqual(kubeconfig["current-context"], "test")


if __name__ == "__main__":
    unittest.main()
