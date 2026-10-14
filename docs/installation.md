# Installation

Three names are deliberately different:

| Thing | Name |
| --- | --- |
| PyPI distribution | `local-terminal-summarizer` |
| Python import package | `summarizer` |
| Console command | `summarize` |

An unrelated project already owns the name `summarizer` on PyPI, so
`pip install summarizer` installs **that** project, not this one. Always
install by `local-terminal-summarizer`.

## Install from PyPI

[pipx](https://pipx.pypa.io/) and [uv](https://docs.astral.sh/uv/) isolate the
package and put `summarize` on your `PATH`:

```sh
pipx install local-terminal-summarizer      # or: uv tool install local-terminal-summarizer
```

With neither, install into a virtualenv instead:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install local-terminal-summarizer
.venv/bin/summarize --version
```

Uninstall with `pipx uninstall local-terminal-summarizer`,
`uv tool uninstall local-terminal-summarizer`, or
`python -m pip uninstall local-terminal-summarizer` in the virtualenv. Upgrade
with `pipx upgrade local-terminal-summarizer` or
`uv tool upgrade local-terminal-summarizer`. There is no self-update mechanism
and the running tool never downloads anything.

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

## From a checkout (development)

```sh
git clone https://github.com/adigaiki/local-terminal-summarizer.git
cd local-terminal-summarizer
pipx install .            # or: uv tool install .
```

Or install editable into a virtualenv and run the tests:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/summarize --version
.venv/bin/python -m pytest
```

## Releases

The package is a normal PEP 621 project with a `summarize` console script.
The version comes from a single source (`summarizer.__version__`), and
`summarize --version` matches the installed package metadata.

Tagged releases are built by `.github/workflows/release.yml`: it produces a
source distribution and a wheel, writes `SHA256SUMS`, and attaches them to the
GitHub release. The build job needs no Ollama server, GPU, or model.

## Publishing to PyPI (maintainers)

Nothing is uploaded automatically, and a version goes to TestPyPI before
PyPI. Upload the artifacts attached to the GitHub release, not a fresh local
build: builds are not byte-reproducible, so a rebuild would put different
bytes on PyPI than the release and its `SHA256SUMS`.

```sh
# Fetch the release artifacts. Never upload dist/* from a local build.
mkdir -p /tmp/rel && cd /tmp/rel
gh release download vX.Y.Z --repo adigaiki/local-terminal-summarizer
sha256sum -c SHA256SUMS                 # both files must report OK
uvx twine check *.whl *.tar.gz

# 1. TestPyPI (test.pypi.org only)
uvx twine upload --repository testpypi *.whl *.tar.gz
python3 -m venv --clear /tmp/ttp
/tmp/ttp/bin/pip install --no-cache-dir \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ local-terminal-summarizer==X.Y.Z
/tmp/ttp/bin/summarize --version

# 2. PyPI (only after step 1 is verified)
uvx twine upload *.whl *.tar.gz
```

Both repositories take an API token as `TWINE_USERNAME=__token__` with the
token in `TWINE_PASSWORD`. Read the token into the environment rather than
typing it on the command line, and unset it afterwards.

The version comes from `summarizer.__version__`; bump it once, and the wheel,
sdist, and `summarize --version` all agree.

## Packaging checks

```sh
python -m build --no-isolation     # sdist + wheel
```

CI builds and installs the distributions in a separate job so a packaging
regression (bad metadata, missing package data) fails the build.
