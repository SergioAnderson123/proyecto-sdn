"""Build a self-contained HA app from canonical sources (also works on Windows)."""
import argparse
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[2]
def build(destination):
    sources = list((root/'R1/shared/policy_mapper').glob('*.py'))
    sources += [root/'R2/policy'/name for name in ('policy_tables.py', 'excepciones.py', 'portal_auth.py', 'policy.yaml')]
    with tarfile.open(destination, 'w:gz') as bundle:
        for source in sorted(sources):
            bundle.add(source, arcname=source.name)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    build(parser.parse_args().output)
