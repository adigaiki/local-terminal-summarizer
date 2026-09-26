# Installation

## Not on PyPI yet

The project is not published to PyPI. Install it from a checkout.

## Recommended: pipx

[pipx](https://pipx.pypa.io/) isolates the package and puts `summarize` on
your `PATH`:

```sh
git clone https://github.com/adigaiki/local-terminal-summarizer.git
cd local-terminal-summarizer
pipx install .
```

Upgrade later with `pipx upgrade summarizer` after pulling a new checkout.
There is no self-update mechanism and the running tool never downloads
anything.

## Virtualenv

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/summarize --version
```

## Optional extras

```sh
pip install 'summarizer[pdf]'   # PDF text extraction (pypdf + fonttools, both BSD-3-Clause)
pip install 'summarizer[ocr]'   # OCR: adds pytesseract + pdf2image
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

The package is PyPI-ready but has not been published. To publish a release:

```sh
python -m build --no-isolation
python -m twine check dist/*
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
