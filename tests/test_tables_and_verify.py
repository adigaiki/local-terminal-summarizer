"""Structured table extraction and mechanical claim verification."""

from __future__ import annotations

import json

from summarizer.config import Config, EngineSettings, InputSettings
from summarizer.document.tables import count_structured_tables, structure_tables
from summarizer.engine import EngineCapabilities
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.verify import verify_summary

# The exact shape pypdf produces for the Danisi table: a labelled header line
# followed by rows of bare values.
_DANISI_TABLE = """TABLE 2A: UNIT-CELL PARAMETERS OF CAVANSITE AS A FUNCTION OF P.

Cavansite (m.e.w.)
P (GPa) a (Å) b (Å) c (Å) V (Å 3)
Pamb  9.6622(7) 13.6819(10) 9.8116(11) 1297.06(19)
0.03(5) 9.6611(6) 13.6786(9) 9.8127(10) 1296.74(18)
0.04(5) 9.6593(6) 13.6744(9) 9.8114(10) 1295.94(17)
"""


# --- table structuring ------------------------------------------------------


def test_table_rows_become_explicit_label_value_pairs():
    transformed, count = structure_tables(_DANISI_TABLE)
    assert count == 1
    assert "b (Å): 13.6819(10)" in transformed
    assert "V (Å 3): 1297.06(19)" in transformed
    assert "P (GPa): Pamb" in transformed
    # The bare stream is gone.
    assert "Pamb  9.6622(7) 13.6819(10)" not in transformed


def test_ordinary_prose_is_untouched():
    prose = "This is a paragraph about the Si-O bond.\nIt has two lines.\n"
    transformed, count = structure_tables(prose)
    assert count == 0
    assert transformed == prose


def test_single_label_header_is_not_a_table():
    text = "Length (mm)\n1.2 3.4 5.6\n7.8 9.0 1.2\n"
    _, count = structure_tables(text)
    assert count == 0


def test_mismatched_row_width_is_not_a_table():
    text = "P (GPa) a (Å)\n1.0 2.0 3.0\n4.0 5.0\n"
    _, count = structure_tables(text)
    assert count == 0


def test_count_structured_tables_matches_marker():
    transformed, count = structure_tables(_DANISI_TABLE)
    assert count_structured_tables(transformed) == count


def test_structure_tables_input_setting_defaults_on():
    assert InputSettings().structure_tables is True


# --- verification -----------------------------------------------------------


def test_fabricated_number_is_flagged():
    report = verify_summary("The value was 1234.56 units.", "The value was 9999 units.")
    assert not report.ok
    assert any(c.kind == "number" and "1234.56" in c.text for c in report.unverified)


def test_real_number_is_not_flagged():
    report = verify_summary("The bond is 1.627 Å.", "average values of 1.627 and 1.6004")
    assert report.ok


def test_rounded_number_matches_a_more_precise_source():
    report = verify_summary("about 1.60 Å", "values of 1.6004 Å")
    assert report.ok


def test_invented_acronym_expansion_is_flagged():
    source = "Data were collected in m.e.w. and silicone oil."
    report = verify_summary(
        "M.E.W. (Molecular Ensemble with Water) was used.", source
    )
    assert not report.ok
    assert any(c.kind == "expansion" and "Molecular Ensemble" in c.text for c in report.unverified)


def test_correct_acronym_expansion_is_not_flagged():
    source = "pressure media: m.e.w. and silicone oil (s.o.)"
    report = verify_summary("S.O. (silicone oil) was used.", source)
    assert report.ok


def test_report_renders_and_serializes():
    report = verify_summary("invented 4242.42 here", "nothing relevant")
    text = report.render()
    assert "unverified" in text
    payload = report.to_json()
    assert payload["unverified"]


def test_report_pluralizes_counts():
    # One number checked, no expansions: singular "number".
    one_number = verify_summary("value 1234.56 units", "value 1234.56 units")
    text = one_number.render()
    assert "1 number," in text
    assert "numbers" not in text

    # One expansion checked, no numbers: singular "expansion".
    one_expansion = verify_summary(
        "S.O. (silicone oil) was used.",
        "pressure media: m.e.w. and silicone oil (s.o.)",
    )
    text = one_expansion.render()
    assert "1 expansion checked" in text
    assert "1 expansions" not in text

    # A single flag is one "claim", not "claim(s)".
    assert "1 unverified claim " in verify_summary(
        "invented 4242.42 here", "nothing relevant"
    ).render()


# --- pipeline wiring --------------------------------------------------------


class _ClaimEngine:
    backend = "fake"
    endpoint = "http://127.0.0.1:9"
    model = "fake"
    timeout_seconds = 1.0
    retries = 0
    max_tokens = 1024

    def capabilities(self):
        return EngineCapabilities(context_length=5000)

    def generate(self, *, prompt, json_object=False, temperature=None):
        if json_object:
            return '{"summary": "M.E.W. (Molecular Ensemble with Water); value 4242.42"}'
        return "M.E.W. (Molecular Ensemble with Water); value 4242.42"

    def stream(self, *, prompt, temperature=None):
        yield self.generate(prompt=prompt)

    def health(self):
        return True

    def list_models(self):
        return [self.model]


def _config() -> Config:
    return Config(
        engine=EngineSettings(context_length=5000),
        input=InputSettings(),
    )


def test_pipeline_verification_attaches_and_serializes(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("Data were collected in m.e.w. and silicone oil.", encoding="utf-8")

    result = Pipeline(_config(), diag=Diagnostics(quiet=True), engine=_ClaimEngine()).run(
        str(source), opts=PipelineOptions(stream=False, verify=True)
    )
    assert result.verification is not None
    kinds = {c.kind for c in result.verification.unverified}
    assert "number" in kinds and "expansion" in kinds

    json_result = Pipeline(_config(), diag=Diagnostics(quiet=True), engine=_ClaimEngine()).run(
        str(source),
        opts=PipelineOptions(stream=False, verify=True, output_format="json"),
    )
    payload = json.loads(json_result.text)
    assert payload["verification"]["unverified"]
