# Installation

## Not on PyPI yet

The project is not published to PyPI. Install it from a checkout.

The **planned** distribution name is `local-terminal-summarizer`. Three names
are deliberately different:

| Thing | Name |
| --- | --- |
| PyPI distribution | `local-terminal-summarizer` |
| Python import package | `summarizer` |
| Console command | `summarize` |

An unrelated project already owns the name `summarizer` on PyPI, so
`pip install summarizer` installs **that** project, not this one. Once this is
published, always install by `local-terminal-summarizer`.

## Recommended: pipx

[pipx](https://pipx.pypa.io/) isolates the package and puts `summarize` on
your `PATH`:

```sh
git clone https://github.com/adigaiki/local-terminal-summarizer.git
cd local-terminal-summarizer
pipx install .
```

Upgrade later with `pipx upgrade local-terminal-summarizer` after pulling a
new checkout. There is no self-update mechanism and the running tool never
downloads anything. Uninstall with
`pipx uninstall local-terminal-summarizer` (or
`uv tool uninstall local-terminal-summarizer`).

## Virtualenv

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/summarize --version
```

## Optional extras

```sh
pip install 'local-terminal-summarizer[pdf]'   # PDF text extraction (pypdf + fonttools, both BSD-3-Clause)
pip install 'local-terminal-summarizer[ocr]'   # OCR: adds pytesseract + pdf2image
```

The base install has no runtime dependencies beyond Python itself. OCR additionally needs the
system `tesseract` and Poppler (`pdftoppm`, `pdfinfo`) binaries; missing
components produce an actionable error, and nothing is installed
automatically. `summarize doctor` reports both capabilities, marking an
intentionally uninstalled extra with `!`.

## Releases

The package is a normal PEP 621 project with a `summarize` console script.
The version comes from a single source (`summarizer.__version__`), and
`summarize --version` matches the installed package metadata.

Tagged releases are built by `.github/workflows/release.yml`: it produces a
source distribution and a wheel, writes `SHA256SUMS`, and attaches them to the
GitHub release. The build job needs no Ollama server, GPU, or model.

## Publishing to PyPI

The package is PyPI-ready but has not been published. **TestPyPI first**, then
PyPI. Nothing is uploaded automatically; these are manual steps.

```sh
python -m build --no-isolation
python -m twine check dist/*

# 1. TestPyPI (test.pypi.org only)
python -m twine upload --repository testpypi dist/*
python -m venv /tmp/ttp && /tmp/ttp/bin/pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ local-terminal-summarizer
/tmp/ttp/bin/summarize --version

# 2. PyPI (only after step 1 is verified)
python -m twine upload dist/*
```

The version comes from `summarizer.__version__`; bump it once, and the wheel,
sdist, and `summarize --version` all agree.

## Packaging checks

```sh
python -m build --no-isolation     # sdist + wheel
```

CI builds and installs the distributions in a separate job so a packaging
regression (bad metadata, missing package data) fails the build.
