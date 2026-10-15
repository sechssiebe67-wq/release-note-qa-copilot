"""Named release-note templates: section order, intro/outro, required sections.

A workspace can render its draft with a **template** that controls which
canonical groups appear, in which order, whether an intro or outro block is
added, and which sections are required. Templates are either built in (see
:data:`BUILTIN_TEMPLATES`) or supplied inline and layered over a built-in one.

Only the Python standard library is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .models import GROUP_ORDER, GROUP_TITLES

DEFAULT_TEMPLATE_NAME = "default"

# Keys understood by the template parser. Anything else is rejected so a typo
# never silently drops part of the house template.
KNOWN_TEMPLATE_KEYS = ("name", "description", "sections", "intro", "outro")
KNOWN_SECTION_KEYS = ("key", "title", "required")


@dataclass(frozen=True)
class TemplateSection:
    """One section of a template: a canonical group key plus a required flag."""

    key: str
    required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation of the section."""
        return {
            "key": self.key,
            "title": GROUP_TITLES.get(self.key, self.key.title()),
            "required": self.required,
        }


@dataclass(frozen=True)
class ReleaseTemplate:
    """An ordered, named layout for a release-note draft.

    Attributes
    ----------
    name:
        Short, unique identifier used to attach the template to a draft.
    description:
        Human-readable explanation of the layout.
    sections:
        Ordered :class:`TemplateSection` entries. A section that is empty is
        skipped unless it is ``required``.
    intro:
        Optional Markdown block rendered before the sections.
    outro:
        Optional Markdown block rendered after the sections.
    """

    name: str
    description: str
    sections: Tuple[TemplateSection, ...]
    intro: str = ""
    outro: str = ""

    def section_keys(self) -> Tuple[str, ...]:
        """Return the ordered section keys."""
        return tuple(section.key for section in self.sections)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation of the template."""
        return {
            "name": self.name,
            "description": self.description,
            "sections": [section.to_dict() for section in self.sections],
            "intro": self.intro,
            "outro": self.outro,
        }


class UnknownTemplateError(ValueError):
    """Raised when a requested template name is not a built-in template."""


def _section(key: str, required: bool = False) -> TemplateSection:
    return TemplateSection(key=key, required=required)


# Built-in templates. ``default`` reproduces the historical layout exactly so
# existing drafts are byte-for-byte unchanged when no template is requested.
BUILTIN_TEMPLATES: Dict[str, ReleaseTemplate] = {
    "default": ReleaseTemplate(
        name="default",
        description=(
            "Canonical order - Features, Fixes, Breaking Changes, Other - "
            "with no intro or outro."
        ),
        sections=tuple(_section(key) for key in GROUP_ORDER),
    ),
    "github-release": ReleaseTemplate(
        name="github-release",
        description=(
            "GitHub Releases layout: Breaking Changes first, then Features, "
            "Fixes and Other, with a 'What's Changed' intro. Requires a "
            "Features section."
        ),
        sections=(
            _section("breaking"),
            _section("feature", required=True),
            _section("fix"),
            _section("other"),
        ),
        intro="## What's Changed",
        outro="**Full Changelog**: compare against the previous release tag.",
    ),
    "email": ReleaseTemplate(
        name="email",
        description=(
            "Short e-mail digest that leads with Fixes and drops empty "
            "Breaking Changes."
        ),
        sections=(_section("fix"), _section("feature"), _section("breaking")),
        intro="Hi everyone, here is what shipped in this release:",
        outro="Thanks,\nThe Release Team",
    ),
}


def default_template() -> ReleaseTemplate:
    """Return the built-in ``default`` template."""
    return BUILTIN_TEMPLATES[DEFAULT_TEMPLATE_NAME]


def normalize_template_name(value: Any) -> Optional[str]:
    """Return a trimmed template name, or ``None`` for an absent/empty one.

    Raises :class:`ValueError` when a non-string value is supplied.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("'template' must be a string")
    name = value.strip()
    return name or None


def get_template(name: str) -> ReleaseTemplate:
    """Return the built-in template called ``name``.

    Raises :class:`UnknownTemplateError` (a :class:`ValueError`) for an unknown
    or empty name.
    """
    if not isinstance(name, str) or not name.strip():
        raise UnknownTemplateError("template name is required")
    key = name.strip()
    template = BUILTIN_TEMPLATES.get(key)
    if template is None:
        raise UnknownTemplateError("unknown template: {!r}".format(key))
    return template


def list_templates() -> List[Dict[str, Any]]:
    """Return the built-in templates as JSON-serialisable summaries."""
    return [
        BUILTIN_TEMPLATES[name].to_dict() for name in sorted(BUILTIN_TEMPLATES)
    ]


def _parse_sections(value: Any) -> Tuple[TemplateSection, ...]:
    """Validate and normalise a template's ``sections`` list."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("'sections' must be a list of section objects")
    sections: List[TemplateSection] = []
    seen = set()
    for item in value:
        if isinstance(item, str):
            key = item.strip()
            required = False
        elif isinstance(item, Mapping):
            unknown = [key for key in item if key not in KNOWN_SECTION_KEYS]
            if unknown:
                raise ValueError(
                    "unknown template section key(s): {}".format(
                        ", ".join(sorted(unknown))
                    )
                )
            key_value = item.get("key")
            if not isinstance(key_value, str):
                raise ValueError("template section 'key' must be a string")
            key = key_value.strip()
            required = item.get("required", False)
            if not isinstance(required, bool):
                raise ValueError(
                    "template section 'required' must be a boolean"
                )
        else:
            raise ValueError("template sections must be strings or objects")
        if not key:
            raise ValueError("template section keys must be non-empty")
        if key not in GROUP_ORDER:
            raise ValueError(
                "unknown template section: {!r} (expected one of: {})".format(
                    key, ", ".join(GROUP_ORDER)
                )
            )
        if key in seen:
            continue
        seen.add(key)
        sections.append(TemplateSection(key=key, required=required))
    return tuple(sections)


def _merge(base: ReleaseTemplate, inline: Mapping[str, Any]) -> ReleaseTemplate:
    """Layer ``inline`` template fields over ``base``.

    ``sections`` (when present) replaces the base order; ``name``,
    ``description``, ``intro`` and ``outro`` override the base values.
    """
    unknown = [key for key in inline if key not in KNOWN_TEMPLATE_KEYS]
    if unknown:
        raise ValueError(
            "unknown template key(s): {}".format(", ".join(sorted(unknown)))
        )
    name = inline.get("name", base.name)
    description = inline.get("description", base.description)
    intro = inline.get("intro", base.intro)
    outro = inline.get("outro", base.outro)
    for label, value in (
        ("name", name),
        ("description", description),
        ("intro", intro),
        ("outro", outro),
    ):
        if not isinstance(value, str):
            raise ValueError("template '{}' must be a string".format(label))
    name = name.strip() or base.name
    sections = (
        _parse_sections(inline["sections"])
        if "sections" in inline
        else base.sections
    )
    return ReleaseTemplate(
        name=name,
        description=description,
        sections=sections,
        intro=intro,
        outro=outro,
    )


def _resolve_mapping(value: Mapping[str, Any]) -> ReleaseTemplate:
    """Resolve an inline template object, selecting a built-in base by name."""
    raw_name = value.get("name")
    if raw_name is not None and not isinstance(raw_name, str):
        raise ValueError("template 'name' must be a string")
    if isinstance(raw_name, str) and raw_name.strip() in BUILTIN_TEMPLATES:
        base = get_template(raw_name.strip())
    else:
        base = default_template()
    return _merge(base, value)


def resolve_template(
    profile_or_config: Any = None,
    inline_template: Optional[Mapping[str, Any]] = None,
) -> ReleaseTemplate:
    """Resolve a validated :class:`ReleaseTemplate`.

    ``profile_or_config`` accepts ``None``/``""`` (the built-in ``default``), a
    built-in template name, a :class:`ReleaseTemplate`, an inline template
    mapping (whose optional ``name`` selects a built-in base), or a request
    payload whose ``template`` field is resolved instead. ``inline_template``
    is an optional mapping layered over that base: ``sections`` replaces the
    order and the remaining fields override one by one.

    Raises :class:`UnknownTemplateError` for an unknown built-in name and
    :class:`ValueError` for an invalid inline template.
    """
    if isinstance(profile_or_config, Mapping):
        if (
            inline_template is None
            and "template" in profile_or_config
            and not any(
                key in profile_or_config for key in ("sections", "intro", "outro")
            )
        ):
            return resolve_template(profile_or_config.get("template"))
        base = _resolve_mapping(profile_or_config)
    elif profile_or_config is None:
        base = default_template()
    elif isinstance(profile_or_config, ReleaseTemplate):
        base = profile_or_config
    elif isinstance(profile_or_config, str):
        base = (
            default_template()
            if not profile_or_config.strip()
            else get_template(profile_or_config)
        )
    else:
        raise ValueError("'template' must be a name, object or null")

    if inline_template is None:
        return base
    if not isinstance(inline_template, Mapping):
        raise ValueError("'template' overrides must be an object")
    return _merge(base, inline_template)


def resolve_template_from_payload(payload: Any) -> ReleaseTemplate:
    """Resolve the optional ``template`` field of a request payload.

    ``payload`` is the request body mapping. An absent/empty ``template``
    resolves to the built-in ``default`` template.
    """
    if not isinstance(payload, Mapping):
        return default_template()
    return resolve_template(payload.get("template"))


def template_from_dict(payload: Any) -> ReleaseTemplate:
    """Rebuild a template from a stored summary (or pass one through)."""
    if payload is None:
        return default_template()
    if isinstance(payload, ReleaseTemplate):
        return payload
    if not isinstance(payload, Mapping):
        raise ValueError("template must be an object")
    return _resolve_mapping(payload)


def template_summary(template: Any = None) -> Dict[str, Any]:
    """Return the effective template as a JSON-serialisable mapping."""
    if template is None:
        template = default_template()
    elif not isinstance(template, ReleaseTemplate):
        template = resolve_template(template)
    return template.to_dict()


__all__ = [
    "DEFAULT_TEMPLATE_NAME",
    "TemplateSection",
    "ReleaseTemplate",
    "UnknownTemplateError",
    "BUILTIN_TEMPLATES",
    "default_template",
    "normalize_template_name",
    "get_template",
    "list_templates",
    "resolve_template",
    "resolve_template_from_payload",
    "template_from_dict",
    "template_summary",
]
