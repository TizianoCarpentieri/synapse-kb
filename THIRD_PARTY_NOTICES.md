# Third-party notices

The source code does not vendor third-party Python libraries. Optional PDF
extraction uses pypdf, pinned to 6.21.1 for the prepared tests/build.

The portable `atlas.pyz` bundles the installed pypdf package, its upstream license
at `LICENSES/pypdf.txt`, and the Synapse KB license. `BUNDLE.json` records versions.
The installed package's actual license is copied by pack.py; do not remove it.

The HTML graph viewer is included project code and uses standard browser APIs.
It does not bundle a graph library, external font or CDN dependency. The names
of external file formats, tools and instruction conventions do not imply endorsement.

Original documents indexed by users retain their own rights. Only the fictional
demo and synthetic fixtures are intended as public example corpora.
