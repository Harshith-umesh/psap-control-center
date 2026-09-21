"""RHAIIS-specific additions to the generic Forge UI schema.

Forge owns the declarative ``projects/rhaiis/ui/submit.yaml`` contract.  This
module contains only the small compatibility layer needed by the Control
Center to preserve the former fournos-ui experience for RHAIIS.  Keeping it
outside ``project_ui_schema`` means other Forge projects use only the generic
schema resolver.
"""

from __future__ import annotations

import logging
from typing import Dict

from app.schemas.ui_schema import ProjectUiSchema, UiField, UiOption
from app.services.github_content import fetch_yaml

logger = logging.getLogger(__name__)


def _titleize(key: str) -> str:
    return key.replace("-", " ").replace("_", " ").strip().title()


def augment_schema(schema: ProjectUiSchema) -> None:
    """Add legacy RHAIIS controls not expressible in ``submit.yaml`` yet."""
    engine_defaults: Dict[str, Dict[str, str]] = {}
    try:
        rhaiis_config = fetch_yaml("projects/rhaiis/orchestration/config.d/rhaiis.yaml")
        for engine, data in (rhaiis_config.get("engines") or {}).items():
            if isinstance(data, dict) and isinstance(data.get("images"), dict):
                engine_defaults[str(engine)] = {
                    str(accelerator): str(image)
                    for accelerator, image in data["images"].items()
                    if isinstance(image, str)
                }
    except Exception as exc:
        logger.warning("Failed to resolve RHAIIS engine defaults: %s", exc)

    for mode in schema.modes:
        all_fields = [field for section in mode.sections for field in section.fields]

        infra = next((section for section in mode.sections if section.id == "infra"), None)
        run_settings = next(
            (
                section
                for section in mode.sections
                if "run" in f"{section.id} {section.label}".lower()
                or "setting" in f"{section.id} {section.label}".lower()
            ),
            mode.sections[-1] if mode.sections else None,
        )

        # The shared Basics step already owns the actual cluster selection.
        # Keep RHAIIS aligned with the legacy FourNos form: there is one
        # Cluster field, not a second selector that can drift from it.
        for section in mode.sections:
            if section.id == "model":
                section.label = "Workload"

        if infra is not None and not any(field.key == "gpu_count" for field in infra.fields):
            infra.fields.append(
                UiField(
                    key="gpu_count",
                    label="GPU Count",
                    type="number",
                    default=1,
                    min=1,
                    help="Number of GPUs to reserve. Must be at least the TP size.",
                )
            )
            all_fields.append(infra.fields[-1])

        if run_settings is not None and not any(
            field.key == "prefix_caching" for field in run_settings.fields
        ):
            run_settings.fields.append(
                UiField(
                    key="prefix_caching",
                    label="Prefix Caching",
                    type="boolean",
                    default=False,
                    help=(
                        "Enable runtime prefix caching. The Forge override is "
                        "selected automatically for the chosen engine."
                    ),
                )
            )
            if run_settings is not infra:
                all_fields.append(run_settings.fields[-1])

        for field in all_fields:
            if field.key == "engine":
                for option in field.options:
                    engine = str(option.overrides.get(field.maps_to or "", option.value))
                    images = engine_defaults.get(engine)
                    if images:
                        option.extra = {**option.extra, "images": images}
            elif field.key == "model":
                if not any(option.value == "__custom_model__" for option in field.options):
                    field.options.append(
                        UiOption(
                            value="__custom_model__",
                            label="Custom Model (provide HuggingFace ID)",
                        )
                    )
            elif field.key == "workload":
                # Some Forge entries such as `ci-quick` are complete test
                # presets, so they correctly live in Quick Presets, but they
                # still represent valid workload keys. Expose those keys in
                # Workload Profiles too so users can select the workload
                # without applying the rest of the preset configuration.
                workload_values = {option.value for option in field.options}
                for quick_preset in mode.quick_presets:
                    quick_workload = quick_preset.fills.get("workload")
                    quick_values = (
                        quick_workload
                        if isinstance(quick_workload, list)
                        else [quick_workload]
                    )
                    for value in quick_values:
                        if (
                            not isinstance(value, str)
                            or not value
                            or value in workload_values
                        ):
                            continue
                        field.options.append(
                            UiOption(
                                value=value,
                                label=_titleize(value),
                                overrides={field.maps_to: value} if field.maps_to else {},
                            )
                        )
                        workload_values.add(value)
                if not any(
                    option.value == "__custom_workload__" for option in field.options
                ):
                    field.options.append(
                        UiOption(value="__custom_workload__", label="Custom")
                    )
            elif field.key == "warmup":
                # Match the legacy RHAIIS form, which starts warmup enabled.
                field.default = True
            elif field.key == "benchmark":
                # The legacy form submits a benchmark run by default.
                field.default = True
            elif field.key == "slack":
                # Single-job notifications are always on in the legacy form.
                field.default = True
            elif field.key == "slack_member_id":
                field.required = True
                if not field.help:
                    field.help = "Required when Slack notifications are enabled."
            elif field.key == "compare_version" and field.visible_if is not None:
                # The field is conditional in the single-job form; once
                # Compare Versions is enabled, the comparison target is
                # required for a reproducible submission.
                if field.visible_if.field == "compare_versions":
                    field.required = True

        if mode.id == "single":
            model_section = next(
                (section for section in mode.sections if section.id == "model"), None
            )
            if model_section is not None and not any(
                field.key == "tp_size" for field in model_section.fields
            ):
                model_section.fields.append(
                    UiField(
                        key="tp_size",
                        label="TP Size Override",
                        type="number",
                        default=1,
                        min=1,
                        help=(
                            "Tensor-parallel size. It defaults to the selected "
                            "model and updates the GPU count."
                        ),
                    )
                )
