"""End-to-end source provenance, without a model or network."""
import json

from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from test_pipeline_and_cli import RecordingEngine, config


def test_pipeline_preserves_source_lines_in_chunks_and_json(tmp_path):
    path = tmp_path / "source.py"
    content = "".join(f"# source line {i}: useful content\n\n" for i in range(20))
    path.write_text(content, encoding="utf-8")
    result = Pipeline(config(max_tokens=35), diag=Diagnostics(quiet=True),
                      engine=RecordingEngine()).run(
        str(path), opts=PipelineOptions(output_format="json", stream=False))
    assert result.document.content == content
    assert len(result.chunks) > 1
    for chunk in result.chunks:
        assert chunk.source == str(path)
        assert chunk.total == len(result.chunks)
        assert chunk.start_line == content.count("\n", 0, chunk.start_char) + 1
        assert chunk.end_line == content.count("\n", 0, chunk.end_char - 1) + 1
    envelope = json.loads(result.text)
    assert '"start_line"' in result.text
    assert envelope["document"]["line_count"] == 40


def make_pdf(path, texts):
    """Generate original, tiny text fixtures; no committed binary documents."""
    import pytest
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = pypdf.PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        if text:
            stream = DecodedStreamObject()
            escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            stream.set_data(f'BT /F1 12 Tf 10 200 Td ({escaped}) Tj ET'.encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


def test_pdf_pages_preserve_empty_page_positions(tmp_path):
    from summarizer.document.reader import read_file
    path = tmp_path / 'paper.pdf'
    make_pdf(path, ['First page.', '', 'Third page.'])
    doc = read_file(str(path), diag=Diagnostics(quiet=True))
    pages = doc.content.split('\f')
    assert len(pages) == 3 and pages[1] == ''
    assert doc.metadata['page_starts'] == [0, len(pages[0]) + 1, len(pages[0]) + 2]
    assert doc.provenance_span(doc.metadata['page_starts'][2], len(doc.content))['start_page'] == 3

