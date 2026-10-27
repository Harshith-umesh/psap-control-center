from app.schemas.ui_schema import ProjectUiSchema
from app.services import project_ui_schema


def test_rhaiis_matrix_resolves_named_pipelines_from_aggregate_cpt_file(monkeypatch):
    from app.services import rhaiis_ui_schema

    cpt_catalog = {
        "__cpt": True,
        "cpt-vllm-release": {
            "__description": "vLLM release matrix",
            "__engine": "vllm",
            "__models": {
                "llama-70b/tp4": {
                    "rhaiis.engines.vllm.args.tensor-parallel-size": 4,
                    "__workloads": ["profile1", "profile4"],
                }
            },
            "__workloads": ["profile1", "profile4"],
            "rhaiis.profiler.enabled": True,
        },
        "cpt-rhaiis-3.6-mi355x": {
            "__description": "RHAIIS 3.6 MI355X matrix",
            "__engine": "vllm",
            "__accelerator": "amd",
            "__gpu_type": "mi355x",
            "__clusters": ["mi355x"],
            "__models": {"gpt-oss-120b/tp1": None},
            "__workloads": ["profile1", "profile4", "profile6"],
            "rhaiis.profiler.enabled": True,
        },
    }

    def fake_fetch_yaml(path):
        if path.endswith("cpt.d/cpt.yaml"):
            return cpt_catalog
        if path.endswith("config.d/rhaiis.yaml"):
            return {
                "engines": {
                    "vllm": {"images": {"nvidia": "vllm:default", "amd": "vllm:rocm"}},
                    "sglang": {"images": {"nvidia": "sglang:default"}},
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(
        project_ui_schema,
        "list_yamls",
        lambda directory: [f"{directory}/cpt.yaml"],
    )
    monkeypatch.setattr(project_ui_schema, "fetch_yaml", fake_fetch_yaml)
    monkeypatch.setattr(rhaiis_ui_schema, "fetch_yaml", fake_fetch_yaml)

    schema = ProjectUiSchema.model_validate(
        {
            "project": "rhaiis",
            "modes": [
                {
                    "id": "cpt",
                    "kind": "matrix",
                    "presets_ref": {"dir": "cpt.d"},
                    "matrix": {"marker_key": "__cpt"},
                    "sections": [
                        {
                            "id": "infra",
                            "fields": [
                                {"key": "accelerator", "type": "radio", "maps_to": "rhaiis.accelerator"},
                                {"key": "engine", "type": "radio", "maps_to": "rhaiis.engine"},
                            ],
                        }
                    ],
                },
                {
                    "id": "single",
                    "sections": [
                        {
                            "id": "infra",
                            "fields": [
                                {"key": "accelerator", "type": "radio"},
                                {"key": "engine", "type": "radio"},
                            ],
                        }
                    ],
                },
            ],
        }
    )

    mode = project_ui_schema._resolve_schema("rhaiis", schema, strict=True).modes[0]

    assert [pipeline.key for pipeline in mode.pipelines] == [
        "cpt-vllm-release",
        "cpt-rhaiis-3.6-mi355x",
    ]
    release = mode.pipelines[0]
    assert release.models[0].key == "llama-70b/tp4"
    assert release.models[0].label == "Llama 70B"
    assert release.models[0].tp == 4
    assert release.models[0].overrides == {
        "rhaiis.engines.vllm.args.tensor-parallel-size": 4
    }
    assert release.models[0].workloads == ["profile1", "profile4"]
    assert release.workloads == ["profile1", "profile4"]
    assert release.overrides == {"rhaiis.profiler.enabled": True}

    mi355x = mode.pipelines[1]
    assert mi355x.engine == "vllm"
    assert mi355x.accelerator == "amd"
    assert mi355x.gpu_type == "mi355x"
    assert mi355x.clusters == ["mi355x"]
    assert mi355x.models[0].tp == 1
    assert mi355x.models[0].workloads is None
    assert "__clusters" not in mi355x.overrides

    fields = {field.key: field for section in mode.sections for field in section.fields}
    assert {option.value for option in fields["engine"].options} == {"vllm"}
    assert {option.value for option in fields["accelerator"].options} == {"amd", "nvidia"}
    assert "gpu_count" not in fields

    single_fields = {
        field.key: field
        for section in schema.modes[1].sections
        for field in section.fields
    }
    assert "gpu_count" in single_fields
